from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.observability.canonical import canonical_sha256, short_hash_id


def fingerprint(value: Any) -> str:
    return short_hash_id("fp", value, length=16)


def _normalize_text(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"https?://\S+", "<url>", lowered)
    lowered = re.sub(r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:\d{2})?", "<ts>", lowered)
    lowered = re.sub(r"\d+(?:\.\d+)?", "<num>", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def _bucket_response_length(length: int) -> str:
    if length <= 80:
        return "0_80"
    if length <= 200:
        return "81_200"
    if length <= 600:
        return "201_600"
    return "601_plus"


def _bucket_newlines(newline_count: int) -> str:
    if newline_count <= 0:
        return "0"
    if newline_count <= 2:
        return "1_2"
    if newline_count <= 10:
        return "3_10"
    return "11_plus"


def behavior_features(executor_output: Dict[str, Any]) -> Dict[str, Any]:
    response = executor_output.get("response", {}) or {}
    content = str(response.get("content", ""))
    plan = executor_output.get("plan", []) or []
    tool_calls = executor_output.get("tool_calls", []) or []
    trace = executor_output.get("trace", {})
    trace_signals = trace.get("signals", {}) or {}
    lowered = content.lower()
    first_token = re.split(r"\s+", lowered, maxsplit=1)[0] if lowered else ""
    is_refusal = bool(
        re.search(
            r"\bi don't have an answer\b|\bi do not have an answer\b|\bi can't\b|\bi cannot\b|\bunable to\b|\bdon't know\b|\bdo not know\b",
            lowered,
        )
    )
    has_greeting = bool(re.search(r"^(hello|hi|hey)\b", lowered))
    user_specific = bool(re.search(r"\b(you|your|you're|you’re)\b", lowered))
    first_token_bucket = "greeting" if first_token in {"hello", "hi", "hey"} else ("refusal" if first_token in {"i", "sorry"} and is_refusal else "other")
    tool_names = ("http_get", "fs_read", "fs_write", "shell_exec", "git_commit", "deploy_staging")
    has_json_like = ("{" in content and "}" in content) or '"type":' in lowered
    return {
        "response_type": response.get("type"),
        "len_bucket": _bucket_response_length(len(content)),
        "newline_bucket": _bucket_newlines(content.count("\n")),
        "is_question": "?" in content,
        "is_refusal": is_refusal,
        "has_greeting": has_greeting,
        "user_specific": user_specific,
        "first_token_bucket": first_token_bucket,
        "has_code_fence": "```" in content,
        "has_json_like": has_json_like,
        "mentions_tools": any(name in lowered for name in tool_names),
        "has_step_words": bool(re.search(r"(step\s*\d+)|(\*\*step)", lowered)),
        "tool_calls_count": (0 if len(tool_calls) == 0 else (1 if len(tool_calls) == 1 else (2 if len(tool_calls) <= 3 else 4))),
        "plan_steps_bucket": (0 if len(plan) == 0 else (1 if len(plan) == 1 else (2 if len(plan) <= 3 else 4))),
        "uncertainty": trace_signals.get("uncertainty"),
    }


def behavior_fingerprint(executor_output: Dict[str, Any]) -> str:
    return short_hash_id("fp", behavior_features(executor_output), length=16)


def trace_fingerprint(executor_output: Dict[str, Any], *, retried: bool = False) -> str:
    run_signature = {
        "behavior": behavior_features(executor_output),
        "retried": bool(retried),
    }
    return short_hash_id("fp", run_signature, length=16)
    return short_hash_id("fp", signature, length=16)


def tool_sequence_fingerprint(tool_results: List[Dict[str, Any]]) -> Optional[str]:
    if not tool_results:
        return None
    signature = [
        {
            "tool": item.get("tool"),
            "allowed": bool(item.get("allowed", False)),
            "blocked": not bool(item.get("allowed", False)),
        }
        for item in tool_results
    ]
    return short_hash_id("fp", signature, length=16)


def canonical_content_hash(value: Any) -> str:
    return canonical_sha256(value)
