from pathlib import Path

from apps.api.models import SubmitRequest
from apps.api.routes import get_request, leaderboard, submit_request
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.db import Database


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "spec.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.requests = {}
        self._n = 0
        self.routing_mode = "broadcast"

    def new_request_id(self) -> str:
        self._n += 1
        return f"req_{self._n}"


def test_request_classification_and_typed_leaderboard(tmp_path: Path) -> None:
    state = LocalState(tmp_path)

    r1 = submit_request(SubmitRequest(user_input="build http api endpoint", request_type="auto"), state=state)
    row = state.db.fetchone("SELECT request_type FROM requests WHERE request_id = ?", (r1.request_id,))
    assert row is not None
    assert row["request_type"] == "web_service"

    leaders = leaderboard("web_service", state=state)
    assert leaders["request_type"] == "web_service"
    assert len(leaders["leaders"]) >= 1


def test_typed_weights_are_independent(tmp_path: Path) -> None:
    state = LocalState(tmp_path)

    state.router.update_weight_for_type("web_service", "pod_a", 3.0)
    state.router.update_weight_for_type("coding", "pod_a", 1.0)

    assert state.router.weights_by_type["web_service"]["pod_a"] == 3.0
    assert state.router.weights_by_type["coding"]["pod_a"] == 1.0


def test_auto_routing_transitions_per_request_type(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    state.routing_mode = "auto"

    # Warm up general type enough to switch to weighted.
    submit_request(SubmitRequest(user_input="general one", request_type="general"), state=state)
    submit_request(SubmitRequest(user_input="general two", request_type="general"), state=state)
    r3 = submit_request(SubmitRequest(user_input="general three", request_type="general"), state=state)
    s3 = get_request(r3.request_id, state=state)
    assert s3.runs is not None
    assert len(s3.runs) == 1

    # New type should start at broadcast until it has enough type-specific completions.
    w1 = submit_request(SubmitRequest(user_input="build http endpoint", request_type="web_service"), state=state)
    sw1 = get_request(w1.request_id, state=state)
    assert sw1.runs is not None
    assert len(sw1.runs) == 2
