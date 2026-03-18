from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from apps.api.models import ReplayRequest
from apps.api.routes import replay
from core_runtime.execute import execute_request
from core_runtime.state import build_runtime_state


def _load_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("eval file must contain a JSON list")
    return [dict(item) for item in payload]


def _build_state(state_factory: Callable[[], Any] | None = None) -> Any:
    return state_factory() if state_factory is not None else build_runtime_state()


def run_smoke_eval_pack(path: str | Path, *, state_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    cases = _load_cases(path)
    results: list[dict[str, Any]] = []
    for case in cases:
        state = _build_state(state_factory)
        payload = dict(case.get("payload") or {})
        result = execute_request(payload, state=state)
        expect = dict(case.get("expect") or {})
        passed = True
        mismatches: list[str] = []
        for key, expected in expect.items():
            actual = result.get(key)
            if actual != expected:
                passed = False
                mismatches.append(f"{key}: expected={expected!r} actual={actual!r}")
        results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "pass": passed,
                "mismatches": mismatches,
                "result": result,
            }
        )
    return {
        "suite": "smoke",
        "total": len(results),
        "passed": sum(1 for item in results if item["pass"]),
        "failed": sum(1 for item in results if not item["pass"]),
        "items": results,
    }


def run_replay_eval_pack(path: str | Path, *, state_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    cases = _load_cases(path)
    results: list[dict[str, Any]] = []
    for case in cases:
        state = _build_state(state_factory)
        seed_payload = dict(case.get("seed_payload") or {})
        seed_result = execute_request(seed_payload, state=state)
        run_id = str(seed_result.get("final_winner_run_id") or "")
        if not run_id:
            results.append(
                {
                    "name": str(case.get("name") or "unnamed"),
                    "pass": False,
                    "mismatches": ["missing final_winner_run_id"],
                    "seed_result": seed_result,
                }
            )
            continue
        replay_payload = ReplayRequest.model_validate(dict(case.get("replay") or {}))
        replay_result = replay(run_id, replay_payload, state=state)
        expect = dict(case.get("expect") or {})
        passed = True
        mismatches: list[str] = []
        if expect.get("same_snapshot") is True and replay_result.get("source_snapshot_id") != replay_result.get("replay_snapshot_id"):
            passed = False
            mismatches.append("snapshot ids differ")
        if "canonical_target" in expect:
            actual_target = (
                (replay_result.get("result") or {}).get("canonical_target")
                or seed_result.get("canonical_target")
            )
            if actual_target != expect["canonical_target"]:
                passed = False
                mismatches.append(
                    f"canonical_target: expected={expect['canonical_target']!r} actual={actual_target!r}"
                )
        results.append(
            {
                "name": str(case.get("name") or "unnamed"),
                "pass": passed,
                "mismatches": mismatches,
                "seed_result": seed_result,
                "replay_result": replay_result,
            }
        )
    return {
        "suite": "replay",
        "total": len(results),
        "passed": sum(1 for item in results if item["pass"]),
        "failed": sum(1 for item in results if not item["pass"]),
        "items": results,
    }
