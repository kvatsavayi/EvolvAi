from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_attractor_query_returns_top_trace_fingerprints(tmp_path: Path) -> None:
    db = Database(tmp_path / "attractor.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    pod = pods["pod_a"]
    for idx in range(3):
        pod.run_request(request_id=f"req_{idx}", user_input="hello pod", request_type="general")

    top = db.top_trace_fingerprints(pod_id="pod_a", last_n_runs=10, limit=5)
    assert len(top) >= 1
    assert top[0]["run_count"] >= 3
    assert top[0]["trace_fp"].startswith("fp_")

    top_behavior = db.top_behavior_fingerprints(pod_id="pod_a", last_n_runs=10, limit=5)
    assert len(top_behavior) >= 1
    assert top_behavior[0]["run_count"] >= 3
    assert top_behavior[0]["behavior_fp"].startswith("fp_")
