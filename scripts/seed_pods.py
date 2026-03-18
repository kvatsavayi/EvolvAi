from pathlib import Path

from core.pod.pod import init_default_pods
from core.storage.db import Database

root = Path(__file__).resolve().parents[1]
db = Database(root / "data" / "agent_pods.db")
db.migrate(root / "core" / "storage" / "migrations.sql")
init_default_pods(db, root / "data" / "artifacts")
print("seeded")
