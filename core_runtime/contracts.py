from __future__ import annotations

from typing import Any, TypedDict


class ExecuteRequestPayload(TypedDict, total=False):
    user_input: str
    request_id: str
    request_type: str
    allowed_personas: list[str]
    workspace_refs: list[str]
    max_steps: int
    retry_same_persona_once: bool
    canonical_target: str | None
    auto_commit: bool
    learn_mode: bool
    auto_spawn_on_failure: bool
    spawn_count: int
    forced_pod_id: str | None
    handoff_artifact_id: str | None
    clarification_answers: dict[str, str]


class ExecuteRequestResult(TypedDict, total=False):
    workflow_id: str
    request_id: str
    pod_id: str
    steps: list[dict[str, Any]]
    workflow_graph_artifact_id: str
    planner_artifact_id: str
    decomposition_plan_artifact_id: str
    canonical_target: str | None
    workspace_id: str | None
    workspace_manifest: dict[str, Any] | None
    service: dict[str, Any] | None
    service_url: str | None
    service_hello_url: str | None
    auto_commit: dict[str, Any] | None
    learning: dict[str, Any] | None
    final_winner_run_id: str | None
    final_status: str
    final_pass: bool
    stop_reason: str | None
    spawn: dict[str, Any] | None
    clarification: dict[str, Any] | None


_EXECUTE_RESULT_KEYS = {
    "workflow_id",
    "request_id",
    "pod_id",
    "steps",
    "workflow_graph_artifact_id",
    "workflow_graph_artifact_path",
    "workflow_graph_relative_path",
    "planner_artifact_id",
    "planner_artifact_path",
    "planner_relative_path",
    "planner",
    "decomposition_plan_artifact_id",
    "decomposition_plan_artifact_path",
    "decomposition_plan_relative_path",
    "decomposition_plan",
    "canonical_target",
    "workspace_id",
    "workspace_manifest",
    "service",
    "service_url",
    "service_hello_url",
    "auto_commit",
    "learning",
    "final_winner_run_id",
    "final_status",
    "final_pass",
    "stop_reason",
    "spawn",
    "clarification",
}


def normalize_execute_result(result: dict[str, Any]) -> ExecuteRequestResult:
    normalized: dict[str, Any] = {}
    for key in _EXECUTE_RESULT_KEYS:
        if key in result:
            normalized[key] = result[key]
    normalized.setdefault("steps", [])
    normalized.setdefault("final_status", "failed")
    normalized.setdefault("final_pass", False)
    return normalized
