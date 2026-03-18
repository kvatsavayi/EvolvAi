from pathlib import Path

from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database


def test_artifact_store_put_and_get_json(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    payload = {"a": 1, "b": {"x": True}}
    artifact_id, path = store.put_json(payload)

    assert artifact_id.startswith("art_")
    assert Path(path).exists()
    assert store.get_json(path) == payload


def test_db_helpers_store_pointer_rows(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")

    db.insert_request(
        request_id="req1",
        created_at="2026-02-19T00:00:00Z",
        status="running",
        user_input="hello",
        request_type="general",
        constraints_json=None,
    )
    db.insert_snapshot(
        snapshot_id="snap1",
        request_id="req1",
        created_at="2026-02-19T00:00:01Z",
        snapshot_hash="h1",
        artifact_path="/tmp/snap.json",
        redaction_applied=True,
    )

    row = db.fetchone("SELECT snapshot_hash, artifact_path FROM snapshots WHERE snapshot_id = ?", ("snap1",))
    assert row is not None
    assert row["snapshot_hash"] == "h1"
    assert row["artifact_path"] == "/tmp/snap.json"
