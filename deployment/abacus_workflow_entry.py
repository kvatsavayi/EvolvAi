from __future__ import annotations

from typing import Any

from core_runtime import build_runtime_state, execute_request


def run_abacus_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    state = build_runtime_state()
    return execute_request(payload, state=state)
