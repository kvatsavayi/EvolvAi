from __future__ import annotations

from typing import Any, Dict


def build_judge_payload(
    *,
    snapshot: Dict[str, Any],
    executor_output: Dict[str, Any],
    tool_results: list[Dict[str, Any]],
) -> Dict[str, Any]:
    # Judge-only path. This payload is never used by executor prompt assembly.
    return {
        "snapshot": snapshot,
        "executor_output": executor_output,
        "tool_results": tool_results,
    }
