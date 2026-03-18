from pathlib import Path

from apps.api.models import SubmitRequest
from apps.api.routes import get_request, submit_request
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.db import Database


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "multi.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.requests = {}
        self._n = 0

    def new_request_id(self) -> str:
        self._n += 1
        return f"req_{self._n}"


def test_multi_pod_broadcast_routing_and_winner_selection(tmp_path: Path) -> None:
    state = LocalState(tmp_path)

    submit_resp = submit_request(SubmitRequest(user_input="hello pod", request_type="general"), state=state)
    status = get_request(submit_resp.request_id, state=state)

    assert status.runs is not None
    assert len(status.runs) == 2
    assert status.winner_run_id is not None
    assert status.winner_run_id in status.runs
    assert status.chosen_pod_id in {"pod_a", "pod_b"}

    # Ensure both runs are persisted and belong to distinct pods for broadcast mode.
    rows = state.db.fetchall(
        "SELECT run_id, pod_id FROM runs WHERE request_id = ? ORDER BY pod_id",
        (submit_resp.request_id,),
    )
    assert len(rows) == 2
    assert {str(r["pod_id"]) for r in rows} == {"pod_a", "pod_b"}


def test_broadcast_resilient_when_one_pod_throws(tmp_path: Path) -> None:
    state = LocalState(tmp_path)

    def _boom(_snapshot):
        raise RuntimeError("synthetic pod failure")

    state.pods["pod_a"].run = _boom  # type: ignore[method-assign]

    submit_resp = submit_request(SubmitRequest(user_input="hello pod", request_type="general"), state=state)
    status = get_request(submit_resp.request_id, state=state)

    assert status.status == "completed"
    assert status.runs is not None
    assert len(status.runs) == 2
    assert status.chosen_pod_id == "pod_b"

    failed = state.db.fetchone(
        "SELECT run_id, status, judge_result_id FROM runs WHERE request_id = ? AND pod_id = ?",
        (submit_resp.request_id, "pod_a"),
    )
    assert failed is not None
    assert str(failed["status"]) == "failed"
    jr = state.db.fetchone("SELECT artifact_path FROM judge_results WHERE judge_result_id = ?", (failed["judge_result_id"],))
    assert jr is not None
