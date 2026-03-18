from __future__ import annotations


def classify_request_type(user_input: str, requested_type: str) -> str:
    rt = (requested_type or "").strip().lower()
    if rt and rt != "auto":
        return rt

    text = (user_input or "").lower()
    if any(k in text for k in ["http", "api", "endpoint", "server"]):
        return "web_service"
    if any(k in text for k in ["bug", "test", "pytest", "refactor", "code"]):
        return "coding"
    if any(k in text for k in ["summarize", "research", "analyze", "report"]):
        return "research"
    return "general"
