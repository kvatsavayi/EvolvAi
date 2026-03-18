from __future__ import annotations

import json
import re
from typing import Any, Optional
from hashlib import sha256

FORBIDDEN_SURVIVAL_PATTERNS = [
    r"surviv(al|e)",
    r"selected",
    r"promotion",
    r"you (were|are) picked",
    r"routing weight",
    r"traffic",
    r"bandit",
    r"ranking",
    r"judge score",
    r"score",
    r"reward",
    r"fitness",
    r"objective",
    r"golden answer",
    r"human preference",
    r"benchmark",
    r"previous run output",
    r"earlier answer",
    r"selection outcome",
]

FORBIDDEN_TOOLS = {"shell_exec"}
FORBIDDEN_EXECUTOR_KEYS = {
    "judge_result",
    "scores",
    "tags",
    "failures",
    "routing_weights",
    "external_signals",
    "selector_state",
    "ab_test",
}
ALLOWED_RETRY_FEEDBACK_TYPES = {"schema", "tool_policy", "budget"}


def assert_no_survival_awareness(text: str) -> None:
    violation = detect_survival_awareness_violation(text)
    if violation is not None:
        raise ValueError("reward-firewall violation: survival awareness leakage")


def detect_survival_awareness_violation(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    for pattern in FORBIDDEN_SURVIVAL_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            span = lowered[match.start() : match.end()]
            return {
                "rule_id": "FW_SURVIVAL_AWARENESS_V1",
                "reason_code": "survival_awareness_leakage",
                "offending_span_hash": f"h_{sha256(span.encode('utf-8')).hexdigest()[:16]}",
                "match_features": {
                    "mentions_survival": any(x in lowered for x in ("surviv", "selected", "promotion")),
                    "mentions_reward": any(x in lowered for x in ("reward", "fitness", "score", "objective")),
                    "self_reference": "you" in lowered,
                },
            }
    return None


def assert_tool_intents(tool_calls: list[dict]) -> None:
    for call in tool_calls:
        if call.get("tool") in FORBIDDEN_TOOLS:
            raise ValueError("reward-firewall violation: forbidden tool intent")


def assert_no_forbidden_executor_payload(payload: dict[str, Any]) -> None:
    def _has_forbidden_key(obj: Any) -> bool:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).strip().lower() in FORBIDDEN_EXECUTOR_KEYS:
                    return True
                if _has_forbidden_key(v):
                    return True
            return False
        if isinstance(obj, list):
            return any(_has_forbidden_key(v) for v in obj)
        return False

    if _has_forbidden_key(payload):
        raise ValueError("reward-firewall violation: forbidden evaluator/selector payload")
    payload_text = json.dumps(payload, sort_keys=True).lower()
    assert_no_survival_awareness(payload_text)


def build_safe_retry_message(feedback_type: str, max_tool_calls: Optional[int] = None) -> str:
    if feedback_type not in ALLOWED_RETRY_FEEDBACK_TYPES:
        raise ValueError("retry feedback must be schema, tool_policy, or budget")
    if feedback_type == "schema":
        return "Your output did not validate against schema. Fix the structure."
    if feedback_type == "tool_policy":
        return "You attempted a forbidden tool. Propose an alternative using allowed tools."
    if max_tool_calls is None:
        raise ValueError("budget retry feedback requires max_tool_calls")
    return f"You exceeded max tool calls. Try again with <= {max_tool_calls} tool calls."
