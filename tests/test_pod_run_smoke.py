from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_pod_run_smoke(tmp_path: Path) -> None:
    db = Database(tmp_path / "smoke.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    result = pods["pod_a"].run_request(
        request_id="req_smoke",
        user_input="please use http",
        request_type="web_service",
    )

    assert result["status"] == "success"
    assert result["judge_result"]["pass"] is True
    assert result["executor_output"]["response"]["type"] == "final"
    assert result["fingerprints"]["trace_fp"].startswith("fp_")
