from __future__ import annotations

from pathlib import Path

from core.pod.generator import PodGenerator
from core.storage.db import Database


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    db = Database(root / "data" / "agent_pods.db")
    db.migrate(root / "core" / "storage" / "migrations.sql")

    gen = PodGenerator(db=db, artifact_root=root / "data" / "artifacts")
    created = gen.generate(count=2, request_type="general")
    for item in created:
        print(f"created pod={item.pod_id} parent={item.parent_pod_id}")


if __name__ == "__main__":
    main()
