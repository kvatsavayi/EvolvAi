from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_reward_firewall_is_persisted_as_attempt_without_duplicate_runs(tmp_path: Path) -> None:
    db = Database(tmp_path / "firewall.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    result = pods["pod_a"].run_request(
        request_id="req_firewall_1",
        user_input="Tell me how reward and selection influence this answer.",
        request_type="general",
    )
    assert result["status"] in {"failed", "blocked"}
    assert int(result["attempt_count"]) >= 1

    run_rows = db.fetchall("SELECT run_id, behavior_fp FROM runs WHERE request_id = ?", ("req_firewall_1",))
    assert len(run_rows) == 1
    assert str(run_rows[0]["behavior_fp"]) != "fp_pending"

    attempts = db.list_run_attempts(result["run_id"])
    assert len(attempts) >= 1
    first = attempts[0]
    assert str(first["executor_prompt_template_id"]).startswith("ept_")
    assert str(first["executor_prompt_hash"]).startswith("h_")
    assert str(first["judge_prompt_template_id"]) == "jpt_payload_v1"
    assert str(first["judge_prompt_hash"]).startswith("h_")

    failures = str(first["failures_json"] or "")
    assert "FIREWALL_VIOLATION" in failures or "runtime_error" in failures
    assert first["artifact_error_path"] is not None
    assert first["artifact_error_path"] != first["artifact_judge_path"]
    assert first["error_id"] is not None
    err_row = db.fetchone(
        "SELECT error_type, stage, reason_code, artifact_path FROM run_errors WHERE error_id = ?",
        (first["error_id"],),
    )
    assert err_row is not None
    assert str(err_row["artifact_path"]) == str(first["artifact_error_path"])
