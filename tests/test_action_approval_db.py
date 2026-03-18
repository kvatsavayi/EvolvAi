from pathlib import Path

from core.storage.db import Database


def test_action_approval_and_idempotency_db_helpers(tmp_path: Path) -> None:
    db = Database(tmp_path / "action.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")

    db.create_action_approval(
        approval_id="appr_1",
        created_at="2026-02-19T00:00:00+00:00",
        tool="fs_write",
        request_id=None,
        pod_id=None,
        expires_at=None,
        metadata_json="{}",
    )
    assert db.is_action_approved("appr_1") is False

    db.approve_action(approval_id="appr_1", approved_at="2026-02-19T00:01:00+00:00")
    assert db.is_action_approved("appr_1") is True

    assert db.action_idempotency_exists("k1") is False
    db.insert_action_idempotency(
        idempotency_key="k1",
        created_at="2026-02-19T00:00:00+00:00",
        tool="fs_write",
        run_id=None,
        rollback_hint="delete:/tmp/x",
        status="succeeded",
        metadata_json="{}",
    )
    assert db.action_idempotency_exists("k1") is True
