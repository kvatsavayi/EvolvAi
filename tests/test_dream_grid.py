from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_dream_grid_is_persisted_per_attempt(tmp_path: Path) -> None:
    db = Database(tmp_path / "dream_grid.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    result = pods["pod_a"].run_request(
        request_id="req_dream_1",
        user_input="Say hello in one line",
        request_type="general",
    )
    attempts = db.list_run_attempts(result["run_id"])
    assert len(attempts) >= 1
    att = attempts[0]
    assert att["dream_grid_json"] is not None
    assert str(att["dream_grid_fp"]).startswith("fp_")
    assert int(att["dream_popcount"]) == 20
    assert float(att["dream_density"]) == 0.2
    assert float(att["dream_entropy"]) > 0.0
    assert int(att["dream_largest_component"]) >= 1
    assert 0.0 <= float(att["dream_symmetry"]) <= 1.0


def test_dream_grid_fp_is_stable_for_same_behavior(tmp_path: Path) -> None:
    db = Database(tmp_path / "dream_grid_stable.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    run1 = pods["pod_a"].run_request(
        request_id="req_dream_a",
        user_input="Say hello in one line",
        request_type="general",
    )
    run2 = pods["pod_a"].run_request(
        request_id="req_dream_b",
        user_input="Say hello in one line",
        request_type="general",
    )
    a1 = db.get_winner_attempt(run1["run_id"])
    a2 = db.get_winner_attempt(run2["run_id"])
    assert a1 is not None and a2 is not None
    assert str(a1["dream_grid_fp"]).startswith("fp_")
    assert str(a1["dream_grid_fp"]) == str(a2["dream_grid_fp"])
