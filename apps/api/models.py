from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str
    request_type: str = "general"


class SubmitResponse(BaseModel):
    request_id: str


class RequestStatus(BaseModel):
    request_id: str
    status: str
    chosen_run_id: Optional[str] = None
    winner_run_id: Optional[str] = None
    runs: Optional[list[str]] = None
    chosen_pod_id: Optional[str] = None
    dna_id: Optional[str] = None
    executor_output_artifact_path: Optional[str] = None
    judge_result_artifact_path: Optional[str] = None
    winner_executor_output_artifact_path: Optional[str] = None
    winner_judge_result_artifact_path: Optional[str] = None
    now_slice_artifact_path: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    run_details: Optional[list[Dict[str, Any]]] = None


class ReplayRequest(BaseModel):
    pod_id: Optional[str] = None
    dna_id: Optional[str] = None
    persona_id: Optional[str] = None
    persona_version: Optional[str] = None


class SignalIngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Optional[str] = None
    pod_id: Optional[str] = None
    signal_type: str
    value: float = 1.0
    metadata: Optional[Dict[str, Any]] = None


class ActionApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    request_id: Optional[str] = None
    pod_id: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class PodGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = 1
    request_type: Optional[str] = None


class WorkspaceLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    attempt_id: str
    capabilities: list[str]
    roots: Optional[list[str]] = None
    budgets: Dict[str, int]
    ttl_seconds: int = 900


class WorkspaceWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    path: str
    content: str


class WorkspaceReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    path: str


class WorkspaceListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    path: str


class KnowledgeCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str
    doc_key: str
    title: Optional[str] = None
    summary: str
    extracted_facts: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)


class RouterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str
    current_now_slice: Optional[Dict[str, Any]] = None
    workspace_refs: list[str] = Field(default_factory=list)
    allowed_personas: list[str] = Field(default_factory=list)


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str
    request_id: Optional[str] = None
    request_type: str = "general"
    allowed_personas: list[str] = Field(default_factory=list)
    workspace_refs: list[str] = Field(default_factory=list)
    max_steps: int = 4
    retry_same_persona_once: bool = True
    canonical_target: Optional[str] = None
    auto_commit: bool = True
    learn_mode: bool = False
    auto_spawn_on_failure: bool = False
    spawn_count: int = 1
    forced_pod_id: Optional[str] = None
    handoff_artifact_id: Optional[str] = None
    clarification_answers: Dict[str, str] = Field(default_factory=dict)


class WorkflowResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: Dict[str, str] = Field(default_factory=dict)


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str


class WorkspaceFileWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    path: str
    content: str


class CommitProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    target: str  # dna_registry|persona_registry|playbooks|tests
    changes: Dict[str, Any]
    summary: str


class CommitCurateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    approver: str = "curator_v1"


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Optional[str] = None
    query: str
    max_hits: int = 20


class TuningHandoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Optional[str] = None
    run_id: Optional[str] = None
    max_attempts: int = 20
    include_payloads: bool = False
