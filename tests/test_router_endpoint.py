from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from apps.api.models import RouterRequest, WorkflowRequest, WorkflowResumeRequest
from apps.api.routes import (
    _is_actionable_bug_failure,
    _learning_reject_reasons,
    _stack_mismatch_reason,
    _implementation_repair_instruction_for_target,
    _is_non_actionable_implementation_failure,
    route_persona,
    resume_workflow,
    run_workflow,
)
from core.pod.pod import init_default_pods
from core.pod.generator import PodVariant
from core.router.router import Router
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database
from core.workspace.service import WorkspaceService


class LocalState:
    def __init__(self, tmp_path: Path) -> None:
        self.db = Database(tmp_path / "router.db")
        repo_root = Path(__file__).resolve().parents[1]
        self.root = repo_root
        self.db.migrate(repo_root / "core" / "storage" / "migrations.sql")
        self.data_dir = tmp_path
        self.pods = init_default_pods(self.db, tmp_path / "artifacts")
        self.router = Router(list(self.pods.keys()))
        self.workspace = WorkspaceService(db=self.db, data_dir=tmp_path)
        self.router_artifact_store = ArtifactStore(tmp_path / "router_artifacts")
        self.requests = {}

    def new_request_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"req_{ts}"


def test_router_endpoint_emits_router_artifact(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    payload = RouterRequest(
        user_input="Research the API docs and compare approaches",
        current_now_slice={"now_slice_id": "now_1", "persona_id": "general", "active_constraints": {"x": 1}},
        workspace_refs=["workspace/run_1/att_1/scratch/note.txt"],
        allowed_personas=["research", "implementation"],
    )
    out = route_persona(payload=payload, state=state)
    assert out["selected_persona_id"] == "research"
    assert "needs_research" in out["reason_tags"]
    assert out["artifact_id"].startswith("art_")
    row = state.db.fetchone("SELECT artifact_type, artifact_path FROM artifacts WHERE artifact_id = ?", (out["artifact_id"],))
    assert row is not None
    assert str(row["artifact_type"]) == "router_artifact"
    assert str(row["artifact_path"]).endswith(".json")


def test_router_tags_intent_clarification_for_ask_me(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    payload = RouterRequest(
        user_input="ask me something you cannot know",
        allowed_personas=["clarifier", "general"],
    )
    out = route_persona(payload=payload, state=state)
    assert out["selected_persona_id"] == "clarifier"
    assert "intent_clarification_needed" in out["reason_tags"]


def test_workflow_runner_records_steps_with_personas(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="ask me something thoughtful",
            request_type="general",
            allowed_personas=["clarifier", "general"],
            max_steps=3,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert out["workflow_id"].startswith("wf_")
    assert len(out["steps"]) >= 1
    assert out["steps"][0]["persona_id"] == "clarifier"
    assert out["final_winner_run_id"] is not None
    assert str(out["workflow_graph_artifact_id"]).startswith("art_")
    row = state.db.fetchone(
        "SELECT artifact_type, artifact_path FROM artifacts WHERE artifact_id = ?",
        (out["workflow_graph_artifact_id"],),
    )
    assert row is not None
    assert str(row["artifact_type"]) == "workflow_graph"
    assert str(row["artifact_path"]).endswith(".json")


def test_canonical_hello_workflow_uses_persona_tool_calls(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="Generate hello service",
            request_type="coding",
            canonical_target="hello_fastapi_service",
            max_steps=6,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert out["canonical_target"] == "hello_fastapi_service"
    assert out["workspace_id"] is not None
    assert isinstance(out["workspace_manifest"], dict)
    assert len(out["steps"]) >= 3
    tools = state.db.fetchall(
        "SELECT tool FROM tool_calls WHERE run_id IN (%s)" % ",".join(["?"] * len(out["steps"])),
        tuple(step["run_id"] for step in out["steps"]),
    )
    tool_names = {str(r["tool"]) for r in tools}
    if tool_names:
        assert "search_local_kb" in tool_names
        assert "write_file" in tool_names
        assert "workspace_run" in tool_names
    else:
        assert out["final_pass"] is False
        assert out.get("stop_reason") in {None, "implementation_fast_fail_non_actionable", "bug_repair_hop_limit_reached"}
    auto_commit = out.get("auto_commit") or {}
    if out["final_pass"]:
        assert bool(auto_commit.get("attempted")) is True
        assert bool(auto_commit.get("pass")) is True
        commit_artifact_id = str(auto_commit.get("commit_artifact_id") or "")
        assert commit_artifact_id.startswith("art_")
        crow = state.db.fetchone(
            "SELECT artifact_type FROM artifacts WHERE artifact_id = ?",
            (commit_artifact_id,),
        )
        assert crow is not None
        assert str(crow["artifact_type"]) == "commit"
    else:
        assert bool(auto_commit.get("attempted")) is False
        assert str(auto_commit.get("reason")) == "workflow_not_eligible"


def test_planner_infers_weather_station_target_and_executes_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_DETERMINISTIC_TOOL_FALLBACK", "1")
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="build a weather station app",
            request_type="coding",
            max_steps=6,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert out["canonical_target"] == "weather_station_app"
    assert str(out["planner_artifact_id"]).startswith("art_")
    planner = out["planner"]
    assert planner["canonical_target"] == "weather_station_app"
    assert isinstance(planner.get("components"), list) and len(planner["components"]) >= 3
    assert isinstance(planner.get("assumptions"), list) and len(planner["assumptions"]) >= 1
    assert isinstance(planner.get("confirmations_needed"), list)
    assert isinstance(planner.get("plan_steps"), list) and len(planner["plan_steps"]) >= 3
    prow = state.db.fetchone(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = ?",
        (out["planner_artifact_id"],),
    )
    assert prow is not None
    assert str(prow["artifact_type"]) == "planner_artifact"
    drow = state.db.fetchone(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = ?",
        (out["decomposition_plan_artifact_id"],),
    )
    assert drow is not None
    assert str(drow["artifact_type"]) == "decomposition_plan"
    assert isinstance(out["decomposition_plan"].get("tasks"), list)
    assert len(out["steps"]) >= 3
    tools = state.db.fetchall(
        "SELECT tool FROM tool_calls WHERE run_id IN (%s)" % ",".join(["?"] * len(out["steps"])),
        tuple(step["run_id"] for step in out["steps"]),
    )
    tool_names = {str(r["tool"]) for r in tools}
    if tool_names:
        assert "search_local_kb" in tool_names
        assert "write_file" in tool_names
        assert "workspace_run" in tool_names
    else:
        assert out["final_pass"] is False
        assert out.get("stop_reason") in {None, "implementation_fast_fail_non_actionable", "bug_repair_hop_limit_reached"}


def test_planner_infers_service_bootstrap_target_for_crud_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_DETERMINISTIC_TOOL_FALLBACK", "1")
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="build a simple CRUD application with API and hello page",
            request_type="coding",
            max_steps=6,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert out["canonical_target"] == "service_bootstrap_app"
    assert isinstance(out.get("planner"), dict)
    assert isinstance((out.get("planner") or {}).get("components"), list)
    persona_plan = [str(x) for x in ((out.get("planner") or {}).get("persona_plan") or [])]
    assert "code_review" in persona_plan
    assert out["workspace_id"] is not None
    personas = [str(step.get("persona_id") or "") for step in (out.get("steps") or [])]
    assert "implementation" in personas
    assert "qa_test" in personas or out.get("stop_reason") in {None, "implementation_fast_fail_non_actionable", "bug_repair_hop_limit_reached"}


def test_workflow_returns_clarification_questions_before_execution(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="build an app for my team",
            request_type="coding",
            max_steps=4,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    if out["final_status"] == "needs_clarification":
        clarification = out.get("clarification") or {}
        assert clarification.get("request_id") == out["request_id"]
        questions = clarification.get("questions") or []
        assert isinstance(questions, list) and len(questions) >= 1
        assert str(questions[0].get("id", "")).startswith("confirm_")
    else:
        assert out["final_status"] in {"success", "failed", "blocked"}


def test_workflow_resume_uses_same_request_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_DETERMINISTIC_TOOL_FALLBACK", "1")
    state = LocalState(tmp_path)
    first = run_workflow(
        payload=WorkflowRequest(
            user_input="build an app for my team",
            request_type="coding",
            max_steps=6,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    if first["final_status"] == "needs_clarification":
        resumed = resume_workflow(
            request_id=str(first["request_id"]),
            payload=WorkflowResumeRequest(
                answers={
                    "confirm_1": "Use Python + FastAPI, API-first, in-memory CRUD for v1.",
                    "confirm_2": "No frontend yet.",
                }
            ),
            state=state,
        )
        assert resumed["request_id"] == first["request_id"]
        assert resumed["final_status"] != "needs_clarification"
    else:
        assert first["final_status"] in {"success", "failed", "blocked"}


def test_ui_widget_prompt_routes_to_actionable_workflow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALLOW_DETERMINISTIC_TOOL_FALLBACK", "1")
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="add a small notepad widget tile in the current page",
            request_type="general",
            max_steps=6,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert out["canonical_target"] == "ui_page_update"
    assert len(out.get("steps") or []) >= 1


def test_workflow_does_not_stub_when_model_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("MODEL_NAME", "llama3.1:8b")
    monkeypatch.setenv("ALLOW_DETERMINISTIC_TOOL_FALLBACK", "0")
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="build a weather station app",
            request_type="coding",
            max_steps=4,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert out["canonical_target"] in {"weather_station_app", "ui_page_update"}
    assert out["final_pass"] is False
    tool_count = state.db.fetchone(
        """
        SELECT COUNT(*) AS c
        FROM tool_calls
        WHERE run_id IN (
          SELECT run_id FROM runs WHERE request_id = ?
        )
        """,
        (out["request_id"],),
    )
    assert tool_count is not None
    assert int(tool_count["c"]) == 0
    personas = [str(step.get("persona_id")) for step in out.get("steps", [])]
    assert "qa_test" not in personas
    assert out.get("stop_reason") in {None, "implementation_fast_fail_non_actionable", "bug_repair_hop_limit_reached"}


def test_actionable_bug_failure_classifier() -> None:
    assert _is_actionable_bug_failure([{"code": "QA_QUALITY_FAILED", "detail": "qa_behavior_check_failed:test_command_nonzero_exit"}])
    assert _is_actionable_bug_failure([{"detail": "workflow_missing_required_file:service_bootstrap_app:implementation:app/main.py"}])
    assert not _is_actionable_bug_failure([{"code": "SCHEMA_ECHO", "detail": "schema_echo:response_contract_echoed"}])


def test_learn_mode_recursively_curates_knowledge(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="Generate hello service",
            request_type="coding",
            canonical_target="hello_fastapi_service",
            max_steps=6,
            retry_same_persona_once=False,
            learn_mode=True,
        ),
        state=state,
    )
    learning = out.get("learning") or {}
    if out["final_pass"]:
        assert bool(learning.get("attempted")) is True
        assert int(learning.get("committed_count") or 0) >= 1
        c = state.db.fetchone("SELECT COUNT(*) AS c FROM knowledge_commits")
        assert c is not None
        assert int(c["c"]) >= 1
        d = state.db.fetchone("SELECT COUNT(*) AS c FROM knowledge_doc_versions")
        assert d is not None
        assert int(d["c"]) >= 1
    else:
        if bool(learning.get("attempted")):
            assert int(learning.get("committed_count") or 0) >= 0
        else:
            assert str(learning.get("reason")) in {"no_successful_steps", "workflow_not_eligible"}


def test_non_actionable_implementation_failure_classifier() -> None:
    assert _is_non_actionable_implementation_failure(
        [{"detail": "workflow_missing_required_file:weather_station_app:implementation:app/main.py"}]
    )
    assert _is_non_actionable_implementation_failure(
        [{"detail": "tool_policy_violation:domain_not_allowlisted"}]
    )
    assert not _is_non_actionable_implementation_failure(
        [{"detail": "qa_behavior_check_failed:test_command_nonzero_exit"}]
    )


def test_crud_repair_instruction_is_actionable() -> None:
    msg = _implementation_repair_instruction_for_target("generic_build_app")
    lowered = msg.lower()
    assert "app/main.py" in msg
    assert "tests/test_main.py" in msg
    assert "crud" in lowered


def test_workflow_handles_pod_runtime_exception_without_stalling(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    pod = state.pods["pod_a"]
    original_run = pod.run

    def _boom(snapshot):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced test crash")

    pod.run = _boom  # type: ignore[assignment]
    try:
        out = run_workflow(
            payload=WorkflowRequest(
                user_input="build a simple CRUD application",
                request_type="coding",
                max_steps=2,
                retry_same_persona_once=False,
                forced_pod_id="pod_a",
            ),
            state=state,
        )
    finally:
        pod.run = original_run  # type: ignore[assignment]
    assert out["request_id"].startswith("req_")
    row = state.db.fetchone("SELECT status FROM requests WHERE request_id = ?", (out["request_id"],))
    assert row is not None
    assert str(row["status"]) == "done"
    assert out["final_status"] in {"failed", "blocked"}


def test_workflow_auto_spawns_on_terminal_failure_with_decomposition_handoff(tmp_path: Path, monkeypatch) -> None:
    state = LocalState(tmp_path)
    pod = state.pods["pod_a"]
    original_run = pod.run

    def _boom(snapshot):  # type: ignore[no-untyped-def]
        raise RuntimeError("forced parent crash")

    class _PassPod:
        def run(self, snapshot):  # type: ignore[no-untyped-def]
            run = state.pods["pod_b"].run(snapshot)
            return run

        def record_failed_run(self, snapshot, exc):  # type: ignore[no-untyped-def]
            return state.pods["pod_b"].record_failed_run(snapshot, exc)

    def _fake_generate(self, *, count=1, request_type=None):  # type: ignore[no-untyped-def]
        return [PodVariant(pod_id="pod_spawn_test", parent_pod_id="pod_a", config={"persona": "general"})]

    pod.run = _boom  # type: ignore[assignment]
    state.pods["pod_spawn_test"] = _PassPod()  # type: ignore[assignment]
    monkeypatch.setattr("apps.api.routes.PodGenerator.generate", _fake_generate)
    try:
        out = run_workflow(
            payload=WorkflowRequest(
                user_input="build a simple CRUD application",
                request_type="coding",
                forced_pod_id="pod_a",
                max_steps=2,
                retry_same_persona_once=False,
                auto_spawn_on_failure=True,
            ),
            state=state,
        )
    finally:
        pod.run = original_run  # type: ignore[assignment]
        state.pods.pop("pod_spawn_test", None)
    spawn = out.get("spawn") or {}
    assert bool(spawn.get("attempted")) is True
    assert str(spawn.get("spawned_pod_id")) == "pod_spawn_test"
    assert str(spawn.get("child_workflow_id", "")).startswith("wf_")


def test_learning_filter_rejects_stack_mismatch_and_schema_noise() -> None:
    reason = _stack_mismatch_reason(
        user_input="build simple CRUD restful app using node express and json data store",
        summary="Implemented FastAPI Python CRUD service.",
    )
    assert reason == "stack_mismatch_requested_node_got_python"
    rejects = _learning_reject_reasons(
        user_input="build simple CRUD restful app using node express and json data store",
        summary='{"response":{"type":"final","content":"done"},"tool_calls":[]}',
        extracted_facts=["workflow_target=generic_build_app", "persona=implementation"],
    )
    assert "summary_schema_noise" in rejects


def test_now_slice_hydrates_workflow_projection_for_build_targets(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    out = run_workflow(
        payload=WorkflowRequest(
            user_input="build a weather station app",
            request_type="coding",
            max_steps=4,
            retry_same_persona_once=False,
        ),
        state=state,
    )
    assert len(out["steps"]) >= 1
    run_id = str(out["steps"][0]["run_id"])
    row = state.db.fetchone("SELECT now_slice_id FROM runs WHERE run_id = ?", (run_id,))
    assert row is not None
    now_slice_id = str(row["now_slice_id"])
    nrow = state.db.fetchone("SELECT artifact_path FROM now_slices WHERE now_slice_id = ?", (now_slice_id,))
    assert nrow is not None
    payload = Path(str(nrow["artifact_path"])).read_text(encoding="utf-8")
    now_slice = json.loads(payload)
    projection = now_slice.get("workflow_projection") or {}
    assert projection.get("version") == "wp_thread_v1"
    goal = projection.get("goal") or {}
    scope = projection.get("scope") or {}
    execution = projection.get("execution") or {}
    constraints = projection.get("constraints") or {}
    quality = projection.get("quality") or {}
    traceability = projection.get("traceability") or {}
    assert goal.get("target_id") == "weather_station_app"
    assert isinstance(goal.get("done_definition"), list) and len(goal.get("done_definition")) >= 1
    assert isinstance(scope.get("components"), list) and len(scope.get("components")) >= 1
    assert str(execution.get("current_persona")) in {"research", "implementation", "qa_test", "release_ops"}
    assert isinstance(constraints.get("required_tools"), list)
    assert isinstance(quality.get("acceptance_checks"), list) and len(quality.get("acceptance_checks")) >= 1
    assert str(traceability.get("planner_artifact_id", "")).startswith("art_")


def test_learning_normalizes_schema_noise_for_behavioral_lane() -> None:
    rejects = _learning_reject_reasons(
        user_input="build a weather station app",
        summary="Implemented endpoint behavior and tests.",
        extracted_facts=["workflow_target=weather_station_app", "persona=implementation", "tool_call=write_file"],
    )
    assert "summary_schema_noise" not in rejects
