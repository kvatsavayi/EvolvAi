from pathlib import Path

from apps.api.models import SubmitRequest
from apps.api.routes import submit_request
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.db import Database


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "auto.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.requests = {}
        self._n = 0
        self.routing_mode = "auto"

    def new_request_id(self) -> str:
        self._n += 1
        return f"req_{self._n}"


def test_auto_mode_progresses_to_weighted_routing(tmp_path: Path) -> None:
    state = LocalState(tmp_path)

    r1 = submit_request(SubmitRequest(user_input="x", request_type="general"), state=state)
    assert len(state.requests[r1.request_id]["runs"]) == 2

    r2 = submit_request(SubmitRequest(user_input="y", request_type="general"), state=state)
    assert len(state.requests[r2.request_id]["runs"]) == 2

    # After enough completion signals, auto mode should route weighted (single pod).
    r3 = submit_request(SubmitRequest(user_input="z", request_type="general"), state=state)
    assert len(state.requests[r3.request_id]["runs"]) == 1

    # Each request writes at least one external signal.
    total = state.db.total_signal_count()
    assert total >= 3
