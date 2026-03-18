from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_mutation_creates_new_dna_and_lineage(tmp_path: Path) -> None:
    db = Database(tmp_path / "mutation.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    pod = pods["pod_a"]
    baseline_id = pod.dna.dna_id

    mutated = pod.spawn_mutated_dna()
    assert mutated.dna_id != baseline_id

    dna_row = db.fetchone("SELECT dna_id, mutation_id FROM dna_versions WHERE dna_id = ?", (mutated.dna_id,))
    assert dna_row is not None
    assert dna_row["mutation_id"] is not None

    edge_row = db.fetchone(
        "SELECT edge_id, parent_id, child_id, reason FROM lineage_edges WHERE parent_id = ? AND child_id = ? AND reason = 'mutation'",
        (baseline_id, mutated.dna_id),
    )
    assert edge_row is not None

    selected = pod._select_active_dna()
    assert selected.dna_id in {baseline_id, mutated.dna_id}
    # Pass-rate tie at bootstrap should prefer newer version (mutated).
    assert selected.dna_id == mutated.dna_id
