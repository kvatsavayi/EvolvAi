from __future__ import annotations

import json
import re


def normalize_response_content(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""

    md_match = re.search(r"\*\*content:\s*([\s\S]*?)\*\*$", text, flags=re.IGNORECASE)
    if md_match:
        content = md_match.group(1).strip()
        if content:
            return content

    plain_match = re.search(r"(?:^|\s)content:\s*([\s\S]*)$", text, flags=re.IGNORECASE)
    if plain_match:
        content = plain_match.group(1).strip()
        if content:
            return content

    if (text.startswith("{") and text.endswith("}")) or (text.startswith("```") and "content" in text.lower()):
        stripped = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
        try:
            parsed = json.loads(stripped)
        except Exception:
            return text
        if isinstance(parsed, dict):
            content = parsed.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return text
