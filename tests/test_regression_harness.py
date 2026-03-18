from pathlib import Path

from core.pod.pod import init_default_pods
from core.regression.harness import RegressionHarness
from core.storage.db import Database


def test_regression_harness_runs_golden_cases(tmp_path: Path) -> None:
    db = Database(tmp_path / "reg.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    harness = RegressionHarness(repo_root / "golden" / "snapshots.json")
    report = harness.run_for_pod_dna(pod=pods["pod_a"], dna=pods["pod_a"].dna)

    assert report["summary"]["total"] >= 1
    assert report["passed"] is True
    assert isinstance(report["report_hash"], str)
