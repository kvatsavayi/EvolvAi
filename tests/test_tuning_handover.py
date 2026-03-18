from __future__ import annotations

from pathlib import Path

from apps.api.models import TuningHandoverRequest, WorkflowRequest
from apps.api.routes import run_workflow, tuning_handover
from tests.test_router_endpoint import LocalState


def test_tuning_handover_creates_artifact_with_summary(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    workflow = run_workflow(
        payload=WorkflowRequest(
            user_input="build a weather station app",
            request_type="coding",
            max_steps=4,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    out = tuning_handover(
        payload=TuningHandoverRequest(
            request_id=str(workflow["request_id"]),
            max_attempts=12,
            include_payloads=False,
        ),
        state=state,
    )
    assert str(out["artifact_id"]).startswith("art_")
    handover = out["handover"]
    assert handover["request"]["request_id"] == workflow["request_id"]
    assert handover["summary"]["runs_analyzed"] >= 1
    assert handover["summary"]["attempts_analyzed"] >= 1
    row = state.db.fetchone(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = ?",
        (out["artifact_id"],),
    )
    assert row is not None
    assert str(row["artifact_type"]) == "tuning_handover"

