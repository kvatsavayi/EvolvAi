from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_fingerprints_stable_for_same_snapshot_and_dna(tmp_path: Path) -> None:
    db = Database(tmp_path / "stable.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    pod = pods["pod_a"]
    run_1 = pod.run_request(request_id="req_same", user_input="please use http", request_type="general")
    run_2 = pod.run_request(request_id="req_same", user_input="please use http", request_type="general")

    assert run_1["fingerprints"]["trace_fp"] == run_2["fingerprints"]["trace_fp"]
    assert run_1["fingerprints"]["behavior_fp"] == run_2["fingerprints"]["behavior_fp"]
    assert run_1["fingerprints"]["tool_seq_fp"] == run_2["fingerprints"]["tool_seq_fp"]
