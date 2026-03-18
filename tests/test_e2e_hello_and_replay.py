from pathlib import Path

from apps.api.models import ReplayRequest, SubmitRequest
from apps.api.routes import get_request, replay, submit_request
from core.pod.pod import init_default_pods
from core.router.router import Router
from core.storage.db import Database


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "e2e.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.requests = {}
        self._n = 0

    def new_request_id(self) -> str:
        self._n += 1
        return f"req_{self._n}"


def test_hello_pod_end_to_end_visibility(tmp_path: Path) -> None:
    state = LocalState(tmp_path)

    submit_resp = submit_request(SubmitRequest(user_input="hello pod", request_type="general"), state=state)
    assert submit_resp.request_id.startswith("req_")

    status = get_request(submit_resp.request_id, state=state)
    assert status.chosen_run_id is not None
    assert status.chosen_pod_id is not None
    assert status.dna_id is not None
    assert status.executor_output_artifact_path is not None
    assert status.judge_result_artifact_path is not None
    assert not str(status.executor_output_artifact_path).startswith("/")
    assert not str(status.judge_result_artifact_path).startswith("/")

    seed_edge = state.db.fetchone(
        "SELECT edge_id FROM lineage_edges WHERE reason = 'manual_seed' AND child_id = ?",
        (status.dna_id,),
    )
    assert seed_edge is not None


def test_replay_uses_same_snapshot_and_new_artifacts(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    submit_resp = submit_request(SubmitRequest(user_input="hello pod", request_type="general"), state=state)
    status = get_request(submit_resp.request_id, state=state)
    assert status.chosen_run_id is not None
    assert status.result is not None

    source_artifacts = status.result["artifacts"]
    source_snapshot_id = status.result["snapshot_id"]
    chosen_row = state.db.fetchone("SELECT run_id FROM runs WHERE run_id = ?", (status.chosen_run_id,))
    if chosen_row is None:
        chosen_row = state.db.fetchone(
            "SELECT run_id FROM runs WHERE request_id = ? ORDER BY created_at DESC LIMIT 1",
            (submit_resp.request_id,),
        )
    assert chosen_row is not None

    replay_resp = replay(str(chosen_row["run_id"]), ReplayRequest(), state=state)
    assert replay_resp["source_snapshot_id"] == source_snapshot_id
    assert replay_resp["replay_snapshot_id"] == source_snapshot_id

    replay_artifacts = replay_resp["result"]["artifacts"]
    assert replay_artifacts["snapshot_artifact_id"] != source_artifacts["snapshot_artifact_id"]
    assert replay_artifacts["executor_artifact_id"] != source_artifacts["executor_artifact_id"]
    assert replay_artifacts["judge_artifact_id"] != source_artifacts["judge_artifact_id"]
    assert not str(replay_artifacts["snapshot"]).startswith("/")
    assert not str(replay_artifacts["now_slice"]).startswith("/")

    replay_edge = state.db.fetchone(
        "SELECT edge_id FROM lineage_edges WHERE reason = 'replay' AND run_id = ?",
        (replay_resp["replay_run_id"],),
    )
    assert replay_edge is not None


def test_replay_supports_dna_and_persona_overrides(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    submit_resp = submit_request(SubmitRequest(user_input="hello pod", request_type="general"), state=state)
    status = get_request(submit_resp.request_id, state=state)
    assert status.chosen_run_id is not None

    pod = state.pods[str(status.chosen_pod_id)]
    mutated = pod.spawn_mutated_dna()
    replay_resp = replay(
        str(status.chosen_run_id),
        ReplayRequest(dna_id=mutated.dna_id, persona_id="research"),
        state=state,
    )

    assert replay_resp["applied_overrides"]["dna_id"] == mutated.dna_id
    assert replay_resp["applied_overrides"]["persona_id"] == "research"
    assert replay_resp["result"]["dna_id"] == mutated.dna_id
    now_row = state.db.fetchone(
        "SELECT persona_id, persona_version FROM now_slices WHERE now_slice_id = ?",
        (replay_resp["result"]["now_slice_id"],),
    )
    assert now_row is not None
    assert str(now_row["persona_id"]) == "research"
    assert str(now_row["persona_version"]).startswith("pv_")


def test_request_status_expanded_includes_tool_trace_details(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    submit_resp = submit_request(SubmitRequest(user_input="please use http", request_type="general"), state=state)

    status = get_request(submit_resp.request_id, include_run_details=True, state=state)
    assert status.run_details is not None
    assert len(status.run_details) >= 1
    first = status.run_details[0]
    assert "tool_calls" in first
    assert "trace_fp" in first
    assert "behavior_fp" in first
    assert "winner_trace_fp" in first
    assert "winner_behavior_fp" in first
    assert "attempts" in first
    assert first["attempts"] is None

    with_attempts = get_request(submit_resp.request_id, include_run_details=True, include_attempts=True, state=state)
    assert with_attempts.run_details is not None
    assert with_attempts.run_details[0]["attempts"] is not None
