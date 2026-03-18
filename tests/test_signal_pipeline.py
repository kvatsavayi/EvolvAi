from pathlib import Path

from apps.api import routes
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.db import Database


class FakeState:
    def __init__(self, db: Database, router_: Router) -> None:
        self.db = db
        self.router = router_
        self.requests = {}


def test_signal_record_and_weight_update(tmp_path: Path) -> None:
    db = Database(tmp_path / "sig.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    init_default_pods(db, tmp_path / "artifacts")
    db.insert_request(
        request_id="req_1",
        created_at="2026-02-19T00:00:00+00:00",
        status="done",
        user_input="x",
        request_type="general",
        constraints_json=None,
    )

    router_obj = Router(["pod_a", "pod_b"])
    state = FakeState(db, router_obj)

    base = router_obj.weights["pod_a"]
    routes._record_signal(
        state=state,
        request_id="req_1",
        pod_id="pod_a",
        request_type="general",
        signal_type="completion",
        value=1.0,
        metadata={"run_id": "run_1"},
    )
    routes._record_signal(
        state=state,
        request_id="req_1",
        pod_id="pod_a",
        request_type="general",
        signal_type="time_to_resolution_ms",
        value=350.0,
        metadata=None,
    )
    updated = routes._update_routing_weight_from_signals(state, "pod_a", "general")
    assert updated > base
