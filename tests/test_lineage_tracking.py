from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_lineage_edges_recorded(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    result = pods["pod_a"].run_request(request_id="req_lineage", user_input="hello", request_type="general")
    edge = db.fetchone("SELECT edge_id, parent_id, child_id, run_id FROM lineage_edges WHERE run_id = ?", (result["run_id"],))
    assert edge is not None
    assert edge["parent_id"] == result["dna_id"]
    assert edge["child_id"].startswith("art_")

    seed_edge = db.fetchone(
        "SELECT edge_id, parent_type, child_type, reason FROM lineage_edges WHERE child_id = ? AND reason = ?",
        (result["dna_id"], "manual_seed"),
    )
    assert seed_edge is not None
