from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.executor.runner import ExecutorRunner
from core.pod.pod import init_default_pods
from core.storage.db import Database


class FlakyBackend:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        self.calls += 1
        now = datetime.now(timezone.utc).isoformat()
        state = (snapshot.context.state or {}) if snapshot.context else {}
        if "retry_instruction" in state:
            return {
                "executor_output_id": f"xo_{run_id[-12:]}",
                "run_id": run_id,
                "response": {"type": "final", "content": "Hello."},
                "plan": [{"step": 1, "intent": "direct answer"}],
                "tool_calls": [],
                "trace": {"summary": f"retry at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
            }
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": "Step 1: plan\nStep 2: use http_get tool."},
            "plan": [{"step": 1, "intent": "plan first"}],
            "tool_calls": [],
            "trace": {"summary": f"first attempt at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
        }


class AlwaysFailBackend:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        self.calls += 1
        now = datetime.now(timezone.utc).isoformat()
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": "I don't have an answer to that."},
            "plan": [{"step": 1, "intent": "refuse"}],
            "tool_calls": [],
            "trace": {"summary": f"attempt at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def test_retry_loop_repairs_rule_violation_once(tmp_path: Path) -> None:
    db = Database(tmp_path / "retry.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")
    pod = pods["pod_a"]

    backend = FlakyBackend()
    pod.executor = ExecutorRunner(backend=backend)

    result = pod.run_request(
        request_id="req_retry_loop",
        user_input="Say hello in one line",
        request_type="general",
    )

    assert backend.calls == 2
    assert result["retried"] is True
    assert result["status"] == "success"
    assert result["judge_result"]["pass"] is True
    attempts = db.list_run_attempts(result["run_id"])
    assert len(attempts) == 2
    assert attempts[0]["executor_output_id"] != attempts[1]["executor_output_id"]
    assert attempts[0]["judge_result_id"] != attempts[1]["judge_result_id"]
    assert int(attempts[0]["latency_ms"]) >= 0
    assert int(attempts[1]["latency_ms"]) >= 0
    assert int(attempts[0]["latency_ms"]) + int(attempts[1]["latency_ms"]) >= 0
    assert attempts[0]["scores_json"] is not None
    assert attempts[0]["tags_json"] is not None
    assert attempts[0]["executor_prompt_template_id"] is not None
    assert str(attempts[0]["executor_prompt_hash"]).startswith("h_")
    assert attempts[0]["judge_prompt_template_id"] == "jpt_payload_v1"
    assert str(attempts[0]["judge_prompt_hash"]).startswith("h_")
    assert attempts[0]["retry_prompt_hash"] is None
    assert attempts[1]["retry_prompt_template_id"] == "rpt_retry_instruction_v1"
    assert str(attempts[1]["retry_prompt_hash"]).startswith("h_")
    assert attempts[0]["inference_params_json"] is not None
    assert attempts[0]["token_counts_json"] is not None
    assert int(attempts[0]["context_truncated"]) == 0
    winner_attempt = db.get_winner_attempt(result["run_id"])
    assert winner_attempt is not None
    assert int(winner_attempt["attempt_num"]) == 2

    run_row = db.fetchone(
        "SELECT attempt_count, winner_attempt_num, winner_attempt_id, repaired FROM runs WHERE run_id = ?",
        (result["run_id"],),
    )
    assert run_row is not None
    assert int(run_row["attempt_count"]) == 2
    assert int(run_row["winner_attempt_num"]) == 2
    assert run_row["winner_attempt_id"] is not None
    assert bool(run_row["repaired"]) is True

    edge = db.fetchone(
        """
        SELECT edge_id
        FROM lineage_edges
        WHERE reason = 'repair_retry'
          AND run_id = ?
          AND parent_type = 'run_attempt'
          AND child_type = 'run_attempt'
        """,
        (result["run_id"],),
    )
    assert edge is not None


def test_retry_loop_marks_retried_but_not_repaired_when_both_attempts_fail(tmp_path: Path) -> None:
    db = Database(tmp_path / "retry_fail.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")
    pod = pods["pod_a"]

    backend = AlwaysFailBackend()
    pod.executor = ExecutorRunner(backend=backend)

    result = pod.run_request(
        request_id="req_retry_fail",
        user_input="ask me something you genuinely dont have an answer for",
        request_type="general",
    )

    assert backend.calls == 2
    assert result["status"] == "failed"
    assert result["retried"] is True
    assert result["repaired"] is False
    assert int(result["attempt_count"]) == 2


def test_retry_message_guides_schema_echo_to_plain_content(tmp_path: Path) -> None:
    db = Database(tmp_path / "retry_schema.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")
    pod = pods["pod_a"]

    msg = pod._build_retry_message(  # type: ignore[attr-defined]
        failures=[{"detail": "schema_echo:response_contract_echoed"}],
        budgets={"max_total_tool_calls": 6},
    )
    lowered = msg.lower()
    assert "strict json" in lowered
    assert "response.content" in lowered
    assert "tool_calls" in lowered
