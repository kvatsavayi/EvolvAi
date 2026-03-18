from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.executor.runner import ExecutorRunner
from core.pod.pod import init_default_pods
from core.storage.db import Database
from core.workspace.service import WorkspaceError, WorkspaceService


class FailingBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "executor_output_id": f"xo_{run_id[-12:]}_f",
            "run_id": run_id,
            "response": {
                "type": "final",
                "content": '{"type":"final","content":"schema echo"}',
            },
            "plan": [{"step": 1, "intent": "answer"}],
            "tool_calls": [],
            "trace": {"summary": f"fail at {now}", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def _setup(tmp_path: Path) -> tuple[Database, dict[str, Any], WorkspaceService]:
    db = Database(tmp_path / "workspace_knowledge.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")
    svc = WorkspaceService(db=db, data_dir=tmp_path)
    return db, pods, svc


def test_workspace_lease_budgets_and_attempt_metrics(tmp_path: Path) -> None:
    db, pods, svc = _setup(tmp_path)
    result = pods["pod_a"].run_request(request_id="req_ws_1", user_input="hello", request_type="general")
    attempt_id = str(result["attempts"][0])
    run_id = str(result["run_id"])

    lease = svc.create_lease(
        run_id=run_id,
        attempt_id=attempt_id,
        capabilities=["read", "write", "search"],
        roots=None,
        budgets={"max_bytes": 64, "max_files": 2, "max_ops": 2, "max_time_seconds": 300},
        ttl_seconds=300,
    )

    write_out = svc.write(lease_id=str(lease["lease_id"]), path="note.txt", content="hello")
    assert write_out["bytes_written"] == 5
    read_out = svc.read(lease_id=str(lease["lease_id"]), path="note.txt")
    assert read_out["content"] == "hello"

    try:
        svc.list(lease_id=str(lease["lease_id"]), path=".")
        assert False, "expected budget_exceeded_ops"
    except WorkspaceError as exc:
        assert exc.code == "budget_exceeded_ops"

    attempt_row = db.get_run_attempt(attempt_id)
    assert attempt_row is not None
    assert int(attempt_row["workspace_ops_count"]) == 2
    assert int(attempt_row["bytes_written"]) == 5


def test_knowledge_commit_gate_and_search_logging(tmp_path: Path) -> None:
    db, pods, svc = _setup(tmp_path)

    pods["pod_a"].executor = ExecutorRunner(backend=FailingBackend())
    failed_result = pods["pod_a"].run_request(request_id="req_fail_1", user_input="hello", request_type="general")
    failed_attempt = str(failed_result["attempts"][0])
    db.execute("UPDATE run_attempts SET pass = 0 WHERE attempt_id = ?", (failed_attempt,))
    failed_lease = svc.create_lease(
        run_id=str(failed_result["run_id"]),
        attempt_id=str(failed_attempt),
        capabilities=["index", "search"],
        roots=None,
        budgets={"max_bytes": 1024, "max_files": 10, "max_ops": 10, "max_time_seconds": 300},
        ttl_seconds=300,
    )
    blocked_commit = svc.commit_knowledge(
        lease_id=str(failed_lease["lease_id"]),
        doc_key="ws/failing",
        title="Fail",
        summary="summary from failed attempt",
        extracted_facts=["fact"],
        source_artifact_ids=["art_src_1"],
    )
    assert blocked_commit["pass"] is False
    assert "judge_pass_required" in blocked_commit["reason"]

    pass_result = pods["pod_b"].run_request(request_id="req_pass_1", user_input="hello", request_type="general")
    pass_attempt = str(pass_result["attempts"][0])
    pass_lease = svc.create_lease(
        run_id=str(pass_result["run_id"]),
        attempt_id=pass_attempt,
        capabilities=["index", "search"],
        roots=None,
        budgets={"max_bytes": 1024, "max_files": 10, "max_ops": 10, "max_time_seconds": 300},
        ttl_seconds=300,
    )

    committed = svc.commit_knowledge(
        lease_id=str(pass_lease["lease_id"]),
        doc_key="ws/passing",
        title="Passing",
        summary="hello request answer notes",
        extracted_facts=["hello is greeting"],
        source_artifact_ids=["art_src_2"],
    )
    assert committed["pass"] is True

    search_out = svc.search_knowledge(query="greeting", limit=5, lease_id=str(pass_lease["lease_id"]))
    assert len(search_out["items"]) >= 1
    assert any(item["doc_key"] == "ws/passing" for item in search_out["items"])

    attempt_row = db.get_run_attempt(pass_attempt)
    assert attempt_row is not None
    assert int(attempt_row["knowledge_commit_attempts"]) == 1
    assert int(attempt_row["knowledge_commits_count"]) == 1
    assert float(attempt_row["knowledge_commit_pass_rate"]) == 1.0
    assert int(attempt_row["knowledge_reads_count"]) == 1
