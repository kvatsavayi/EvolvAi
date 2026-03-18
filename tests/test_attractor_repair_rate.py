from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.api.routes import list_attractors
from core.executor.runner import ExecutorRunner
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.db import Database


class RepairSkewBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        state = (snapshot.context.state or {}) if snapshot.context else {}
        needs_repair = snapshot.request_id.startswith("req_repair_")
        if "retry_instruction" in state:
            return {
                "executor_output_id": f"xo_{run_id[-12:]}_r",
                "run_id": run_id,
                "response": {"type": "final", "content": "Hello."},
                "plan": [{"step": 1, "intent": "answer"}],
                "tool_calls": [],
                "trace": {"summary": f"repair at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
            }
        if needs_repair:
            return {
                "executor_output_id": f"xo_{run_id[-12:]}_f",
                "run_id": run_id,
                "response": {"type": "final", "content": "Step 1: plan\nStep 2: use http_get tool."},
                "plan": [{"step": 1, "intent": "plan"}],
                "tool_calls": [],
                "trace": {"summary": f"first fail at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
            }
        return {
            "executor_output_id": f"xo_{run_id[-12:]}_p",
            "run_id": run_id,
            "response": {"type": "final", "content": "Hello."},
            "plan": [{"step": 1, "intent": "answer"}],
            "tool_calls": [],
            "trace": {"summary": f"first pass at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
        }


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "attractor_repair.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.requests = {}


def test_attractors_reports_repair_rate_for_shared_behavior(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    pod = state.pods["pod_a"]
    pod.executor = ExecutorRunner(backend=RepairSkewBackend())

    for idx in range(10):
        request_id = f"req_repair_{idx}" if idx < 4 else f"req_clean_{idx}"
        pod.run_request(
            request_id=request_id,
            user_input="Say hello in one line",
            request_type="general",
        )

    payload = list_attractors(window=50, group_by="global", sort="frequency", limit=10, min_count=1, state=state)
    hello_item = next((item for item in payload["items"] if item["behavior_fp"].startswith("fp_")), None)
    assert hello_item is not None
    assert hello_item["count_total"] == 10
    assert hello_item["count_success"] == 10
    assert hello_item["count_failed"] == 0
    assert hello_item["count_retried"] == 4
    assert hello_item["count_repaired"] == 4
    assert hello_item["retry_rate"] == 0.4
    assert hello_item["repair_rate"] == 0.4
    assert hello_item["repair_success_rate"] == 1.0
    assert hello_item["avg_attempt_count"] == 1.4
    assert hello_item["median_attempt_count"] == 1
    assert isinstance(hello_item["median_latency_ms"], int)
    assert isinstance(hello_item["p95_latency_ms"], int)
    assert hello_item["median_latency_ms"] >= 0
    assert hello_item["p95_latency_ms"] >= hello_item["median_latency_ms"]
    assert hello_item["features"] is not None
    assert "is_question" in hello_item["features"]
    assert "user_specific" in hello_item["features"]
    assert len(hello_item["sample"]) >= 1
    assert hello_item["sample"][0]["winner_attempt_id"] is not None

    filtered = list_attractors(window=50, group_by="global", sort="frequency", limit=10, min_count=11, state=state)
    assert filtered["items"] == []
