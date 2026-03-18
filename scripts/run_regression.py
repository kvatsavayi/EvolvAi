from __future__ import annotations

from pathlib import Path

from core.pod.pod import init_default_pods
from core.regression.harness import RegressionHarness
from core.storage.db import Database


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db = Database(root / "data" / "agent_pods.db")
    db.migrate(root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, root / "data" / "artifacts")

    harness = RegressionHarness(root / "golden" / "snapshots.json")
    for pod_id, pod in pods.items():
        report = harness.run_for_pod_dna(pod=pod, dna=pod.dna)
        print(f"{pod_id}: passed={report['passed']} summary={report['summary']}")


if __name__ == "__main__":
    main()
