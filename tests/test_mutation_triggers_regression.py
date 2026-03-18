from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_mutation_triggers_regression_and_stores_report(tmp_path: Path) -> None:
    db = Database(tmp_path / "mut_reg.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    pod = pods["pod_a"]
    mutated = pod.spawn_mutated_dna()

    assert "regression_passed" in mutated.lineage
    row = db.fetchone(
        "SELECT artifact_id, artifact_type FROM artifacts WHERE artifact_type = 'regression_report' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
