"""LLM Normalizer: uses an LLM to normalize model outputs for fair comparison.

Instead of brittle regex rules, the normalizer converts model outputs into
canonical forms so that semantically equivalent answers (e.g. "9,386" vs "9386")
are recognised as matching.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class NormalizationType(str, Enum):
    NUMBER = "number"
    DATE = "date"
    CODE = "code"
    TEXT = "text"
    JSON_TYPE = "json"
    AUTO = "auto"


# ─────────────────────────────────────────────────────────────────────────
# Cache for normalizations
# ─────────────────────────────────────────────────────────────────────────

class _NormCache:
    """Simple in-memory LRU-ish cache for normalizations."""

    def __init__(self, max_size: int = 1024):
        self._store: Dict[str, str] = {}
        self._max_size = max_size

    def _key(self, text: str, norm_type: str) -> str:
        return hashlib.sha256(f"{norm_type}:{text}".encode()).hexdigest()[:32]

    def get(self, text: str, norm_type: str) -> Optional[str]:
        return self._store.get(self._key(text, norm_type))

    def put(self, text: str, norm_type: str, result: str) -> None:
        if len(self._store) >= self._max_size:
            # evict oldest quarter
            keys = list(self._store.keys())
            for k in keys[: len(keys) // 4]:
                self._store.pop(k, None)
        self._store[self._key(text, norm_type)] = result

    def clear(self) -> None:
        self._store.clear()


_cache = _NormCache()


# ─────────────────────────────────────────────────────────────────────────
# Fast local normalizers (no LLM needed)
# ─────────────────────────────────────────────────────────────────────────

def _normalize_number_local(text: str) -> Optional[str]:
    """Extract and normalise a numeric value from text.

    Handles commas, decimals, percentages, units, and embedded numbers.
    Returns the canonical string representation, or None if no number found.
    """
    cleaned = text.strip().lower()

    # Direct numeric string (possibly with commas)
    m = re.search(r'-?[\d,]+\.?\d*', cleaned.replace(' ', ''))
    if m:
        num_str = m.group(0).replace(',', '')
        try:
            val = float(num_str)
            # Return integer form if it's a whole number
            if val == int(val):
                return str(int(val))
            return str(val)
        except ValueError:
            pass

    return None


def _normalize_date_local(text: str) -> Optional[str]:
    """Try to parse common date formats into YYYY-MM-DD."""
    import re
    patterns = [
        (r'(\d{4})-(\d{1,2})-(\d{1,2})', lambda m: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"),
        (r'(\d{1,2})/(\d{1,2})/(\d{4})', lambda m: f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"),
        (r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', None),  # handled below
    ]
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8,
        'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    cleaned = text.strip()

    # ISO
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', cleaned)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # US numeric
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', cleaned)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # Textual month
    m = re.search(r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', cleaned, re.IGNORECASE)
    if m:
        month_name = m.group(1).lower()
        if month_name in months:
            return f"{m.group(3)}-{months[month_name]:02d}-{int(m.group(2)):02d}"

    return None


def _extract_code_block(text: str) -> str:
    """Extract code from markdown code blocks, or return cleaned text."""
    # Try triple-backtick blocks
    m = re.search(r'```(?:\w+)?\s*\n?(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try single-backtick
    m = re.search(r'`([^`]+)`', text)
    if m:
        return m.group(1).strip()
    return text.strip()


def _normalize_json_local(text: str) -> Optional[str]:
    """Try to parse and re-serialize JSON for canonical comparison."""
    cleaned = text.strip()
    # Try to find JSON in code blocks
    code = _extract_code_block(cleaned) if '```' in cleaned or '`' in cleaned else cleaned
    try:
        parsed = json.loads(code)
        return json.dumps(parsed, sort_keys=True, separators=(',', ':'))
    except json.JSONDecodeError:
        # Try to find JSON object/array in text
        for pattern in [r'\{[^{}]*\}', r'\[.*?\]']:
            m = re.search(pattern, cleaned, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    return json.dumps(parsed, sort_keys=True, separators=(',', ':'))
                except json.JSONDecodeError:
                    continue
    return None


def _detect_type(text: str) -> NormalizationType:
    """Auto-detect the normalization type for a piece of text."""
    cleaned = text.strip().lower()

    # Numbers
    if re.match(r'^-?[\d,]+\.?\d*\s*(%|km|kg|ms|mph|km/h)?$', cleaned.replace(' ', '')):
        return NormalizationType.NUMBER

    # JSON
    stripped = text.strip()
    if (stripped.startswith('{') and stripped.endswith('}')) or \
       (stripped.startswith('[') and stripped.endswith(']')):
        return NormalizationType.JSON_TYPE

    # Code
    if '```' in text or 'def ' in text or 'function ' in text or 'class ' in text:
        return NormalizationType.CODE

    # Date patterns
    if re.search(r'\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}', text):
        return NormalizationType.DATE

    return NormalizationType.TEXT


# ─────────────────────────────────────────────────────────────────────────
# LLM-backed normalizer
# ─────────────────────────────────────────────────────────────────────────

class LLMNormalizer:
    """Normalizes model outputs using a combination of local heuristics and LLM.

    For simple cases (numbers, dates, JSON), local normalization is used for speed.
    For semantic text comparison, the LLM is invoked to extract the canonical answer.
    """

    def __init__(
        self,
        *,
        llm_model: str = "CLAUDE_V3_5_SONNET",
        use_llm_fallback: bool = True,
        cache_enabled: bool = True,
    ):
        self.llm_model = llm_model
        self.use_llm_fallback = use_llm_fallback
        self.cache_enabled = cache_enabled
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import abacusai
                self._client = abacusai.ApiClient()
            except Exception:
                self._client = None
        return self._client

    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call the LLM for normalization."""
        client = self._get_client()
        if client is None:
            return None
        try:
            resp = client.evaluate_prompt(
                prompt=prompt,
                llm_name=self.llm_model,
                max_tokens=256,
                temperature=0.0,
                system_message="You are a precise extraction assistant. Extract only the requested value. Be concise.",
            )
            return str(resp.content or "").strip()
        except Exception:
            return None

    def normalize(
        self,
        text: str,
        norm_type: NormalizationType = NormalizationType.AUTO,
        context: Optional[str] = None,
    ) -> str:
        """Normalize text to a canonical form.

        Args:
            text: The text to normalize (typically a model response).
            norm_type: Type of normalization to apply.
            context: Optional context (e.g., the expected answer) to guide normalization.

        Returns:
            The normalized text.
        """
        if not text or not text.strip():
            return ""

        if norm_type == NormalizationType.AUTO:
            norm_type = _detect_type(text)

        # Check cache
        if self.cache_enabled:
            cached = _cache.get(text, norm_type.value)
            if cached is not None:
                return cached

        result = self._normalize_by_type(text, norm_type, context)

        if self.cache_enabled:
            _cache.put(text, norm_type.value, result)

        return result

    def _normalize_by_type(
        self, text: str, norm_type: NormalizationType, context: Optional[str]
    ) -> str:
        if norm_type == NormalizationType.NUMBER:
            return self._normalize_number(text, context)
        elif norm_type == NormalizationType.DATE:
            return self._normalize_date(text)
        elif norm_type == NormalizationType.CODE:
            return self._normalize_code(text)
        elif norm_type == NormalizationType.JSON_TYPE:
            return self._normalize_json(text)
        else:  # TEXT
            return self._normalize_text(text, context)

    def _normalize_number(self, text: str, context: Optional[str] = None) -> str:
        local = _normalize_number_local(text)
        if local is not None:
            return local

        # LLM fallback: extract the numeric answer
        if self.use_llm_fallback:
            prompt = f"Extract only the numeric answer from this text. Return JUST the number, nothing else.\n\nText: {text}"
            if context:
                prompt += f"\n\nContext (expected format): {context}"
            result = self._call_llm(prompt)
            if result:
                # Try to parse the LLM response as a number
                local2 = _normalize_number_local(result)
                if local2:
                    return local2
                return result.strip()

        return text.strip()

    def _normalize_date(self, text: str) -> str:
        local = _normalize_date_local(text)
        if local is not None:
            return local

        if self.use_llm_fallback:
            result = self._call_llm(
                f"Extract the date from this text and return it in YYYY-MM-DD format. Return JUST the date.\n\nText: {text}"
            )
            if result:
                local2 = _normalize_date_local(result)
                if local2:
                    return local2
                return result.strip()

        return text.strip()

    def _normalize_code(self, text: str) -> str:
        return _extract_code_block(text)

    def _normalize_json(self, text: str) -> str:
        local = _normalize_json_local(text)
        if local is not None:
            return local
        return text.strip()

    def _normalize_text(self, text: str, context: Optional[str] = None) -> str:
        """Normalize free-form text for semantic comparison.

        Uses LLM to extract the core answer when possible.
        """
        # If the text is short, just clean it
        cleaned = text.strip()
        if len(cleaned) < 100 and context is None:
            return cleaned.lower()

        if self.use_llm_fallback and context:
            prompt = (
                f"Given the question context and the model's response, extract ONLY the core answer. "
                f"Be as concise as possible — ideally a single word, number, or short phrase.\n\n"
                f"Expected answer format: {context}\n"
                f"Model response: {text}\n\n"
                f"Core answer:"
            )
            result = self._call_llm(prompt)
            if result:
                return result.strip().lower()

        return cleaned.lower()

    # ─────────────────────────────────────────────────────────────────
    # Semantic comparison
    # ─────────────────────────────────────────────────────────────────

    def are_semantically_equivalent(
        self,
        response: str,
        expected: str,
        norm_type: NormalizationType = NormalizationType.AUTO,
    ) -> Tuple[bool, float, str]:
        """Check if a response is semantically equivalent to the expected answer.

        Returns:
            (is_equivalent, confidence, reasoning)
        """
        if not response.strip() or not expected.strip():
            return False, 0.0, "Empty input"

        # Normalize both sides
        norm_response = self.normalize(response, norm_type, context=expected)
        norm_expected = self.normalize(expected, norm_type)

        # Exact match after normalization
        if norm_response == norm_expected:
            return True, 1.0, "Exact match after normalization"

        # Check if normalized expected is contained in normalized response
        if norm_expected in norm_response:
            return True, 0.95, "Expected answer found within normalized response"

        # For numbers, try numeric comparison
        if norm_type in (NormalizationType.NUMBER, NormalizationType.AUTO):
            try:
                val_r = float(norm_response.replace(',', ''))
                val_e = float(norm_expected.replace(',', ''))
                if abs(val_r - val_e) < 1e-6:
                    return True, 1.0, "Numeric values are equal"
                if val_e != 0 and abs(val_r - val_e) / abs(val_e) < 0.01:
                    return True, 0.95, "Numeric values within 1%"
            except (ValueError, ZeroDivisionError):
                pass

        # For text, use token overlap as a fast heuristic
        resp_tokens = set(norm_response.lower().split())
        exp_tokens = set(norm_expected.lower().split())
        if exp_tokens:
            overlap = len(resp_tokens & exp_tokens) / len(exp_tokens)
            if overlap >= 0.8:
                return True, overlap, f"High token overlap ({overlap:.0%})"

        # LLM-based semantic comparison as last resort
        if self.use_llm_fallback and len(expected) > 2:
            result = self._llm_semantic_compare(response, expected)
            if result is not None:
                return result

        return False, 0.0, "No semantic equivalence detected"

    def _llm_semantic_compare(
        self, response: str, expected: str
    ) -> Optional[Tuple[bool, float, str]]:
        """Use LLM to determine semantic equivalence."""
        prompt = (
            f"Determine if the MODEL RESPONSE contains or is semantically equivalent to the EXPECTED ANSWER.\n\n"
            f"EXPECTED ANSWER: {expected}\n"
            f"MODEL RESPONSE: {response[:500]}\n\n"
            f"Respond with EXACTLY one line in this format:\n"
            f"EQUIVALENT: YES/NO | CONFIDENCE: 0.0-1.0 | REASON: brief explanation"
        )
        result = self._call_llm(prompt)
        if not result:
            return None

        try:
            is_eq = "YES" in result.upper().split("|")[0]
            conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', result, re.IGNORECASE)
            confidence = float(conf_match.group(1)) if conf_match else (0.8 if is_eq else 0.2)
            reason_match = re.search(r'REASON:\s*(.+)', result, re.IGNORECASE)
            reason = reason_match.group(1).strip() if reason_match else "LLM comparison"
            return is_eq, confidence, reason
        except Exception:
            return None

    def clear_cache(self) -> None:
        _cache.clear()
