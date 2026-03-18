from __future__ import annotations

import json
import os
import re
import uuid
import zipfile
from urllib import request as urllib_request
from urllib.error import URLError
from datetime import datetime, timezone
from collections import defaultdict
from math import ceil
from pathlib import Path
from typing import Optional
from statistics import median
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from apps.api.dependencies import AppState, get_state
from apps.api.models import (
    ActionApprovalCreateRequest,
    CommitCurateRequest,
    CommitProposeRequest,
    KnowledgeCommitRequest,
    PodGenerateRequest,
    ResearchRequest,
    ReplayRequest,
    RouterRequest,
    WorkflowRequest,
    RequestStatus,
    SignalIngestRequest,
    SubmitRequest,
    SubmitResponse,
    TuningHandoverRequest,
    WorkflowResumeRequest,
    WorkspaceCreateRequest,
    WorkspaceFileWriteRequest,
    WorkspaceLeaseRequest,
    WorkspaceListRequest,
    WorkspaceWriteRequest,
)
from core.executor.sandbox import detect_survival_awareness_violation
from core.observability.canonical import canonical_sha256
from core.observability.traces import behavior_features
from core.pod.dna import DNA
from core.pod.lineage import make_lineage_edge
from core.pod.pod import Pod
from core.pod.generator import PodGenerator
from core.router.classifier import classify_request_type
from core.snapshot.builder import build_snapshot
from core.snapshot.schema import Snapshot
from core.snapshot.schema import SnapshotPolicies
from core.storage.artifact_store import ArtifactStore
from core.workspace.service import WorkspaceError

router = APIRouter(prefix="/v1", tags=["v1"])


def _winner_key(run: dict) -> tuple:
    passed = bool(run["judge_result"].get("pass", False))
    policy_compliance = float(run["judge_result"].get("scores", {}).get("policy_compliance", 0.0))
    attempt_count = max(1, int(run.get("attempt_count", 1)))
    retry_penalty = -(attempt_count - 1) * 0.2
    tool_penalty = -len(run.get("tool_results", []))
    latency_penalty = -int(run.get("latency_ms", 0))
    return (passed, policy_compliance, retry_penalty, tool_penalty, latency_penalty)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _hydrate_replay_dna(*, state: AppState, pod: Pod, dna_id: str) -> DNA:
    existing = pod.dna_pool.get(dna_id)
    if existing is not None:
        return existing
    row = state.db.fetchone(
        "SELECT artifact_path FROM dna_versions WHERE dna_id = ?",
        (dna_id,),
    )
    if row is None or not row["artifact_path"]:
        raise HTTPException(status_code=404, detail="dna not found")
    dna_payload = _read_json_from_data_path(state, str(row["artifact_path"]))
    dna = DNA(**dna_payload)
    pod.dna_pool[dna.dna_id] = dna
    return dna


def _snapshot_with_replay_overrides(
    *,
    snapshot: Snapshot,
    persona_id: str | None,
    persona_version: str | None,
) -> Snapshot:
    if not persona_id and not persona_version:
        return snapshot
    state_payload = dict(snapshot.context.state or {})
    if persona_id:
        state_payload["persona_id"] = persona_id
    if persona_version:
        state_payload["persona_version"] = persona_version
    return snapshot.model_copy(
        update={
            "context": snapshot.context.model_copy(
                update={"state": state_payload},
            )
        }
    )

def _resolve_data_path(state: AppState, path: str) -> Path:
    candidate = Path(str(path))
    if candidate.is_absolute():
        return candidate
    return (state.data_dir / candidate).resolve()


def _portable_ref_path(state: AppState, path: str | None) -> str | None:
    raw = str(path or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        return candidate.as_posix()
    candidate_roots: list[Path] = []
    data_dir = getattr(state, "data_dir", None)
    root_dir = getattr(state, "root", None)
    if isinstance(data_dir, Path):
        candidate_roots.append(data_dir.resolve())
    if isinstance(root_dir, Path):
        candidate_roots.append(root_dir.resolve())
    for root in candidate_roots:
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except Exception:
            continue
    markers = ("artifacts", "router_artifacts", "workspaces", "workspace", "knowledge", "config", "data")
    parts = list(candidate.parts)
    for marker in markers:
        if marker in parts:
            return Path(*parts[parts.index(marker):]).as_posix()
    return candidate.name


def _normalize_artifact_map(state: AppState, artifacts: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(artifacts or {})
    for key in ("snapshot", "now_slice", "executor_output", "judge_result", "winner_executor_output", "winner_judge_result"):
        if key in normalized:
            normalized[key] = _portable_ref_path(state, str(normalized.get(key) or ""))
    return normalized


def _normalize_request_result(state: AppState, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    if isinstance(normalized.get("artifacts"), dict):
        normalized["artifacts"] = _normalize_artifact_map(state, dict(normalized["artifacts"]))
    return normalized


def _read_json_from_data_path(state: AppState, path: str) -> dict:
    resolved = _resolve_data_path(state, path)
    return json.loads(resolved.read_text(encoding="utf-8"))


def _safe_load_artifact_payload(state: AppState, artifact_path: Optional[str]) -> Optional[dict]:
    if not artifact_path:
        return None
    try:
        payload = _read_json_from_data_path(state, str(artifact_path))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _record_signal(
    *,
    state: AppState,
    request_id: str | None,
    pod_id: str | None,
    request_type: str | None = None,
    signal_type: str,
    value: float,
    metadata: dict | None = None,
) -> str:
    created_at = _now_iso()
    signal_id = f"sig_{abs(hash((created_at, request_id, pod_id, signal_type, value))) % (10**14):014d}"
    state.db.insert_external_signal(
        signal_id=signal_id,
        created_at=created_at,
        request_id=request_id,
        pod_id=pod_id,
        request_type=request_type,
        signal_type=signal_type,
        value=value,
        metadata_json=json.dumps(metadata or {}),
    )
    return signal_id


def _persona_version(state: AppState, persona_id: str) -> str:
    config_dir = getattr(state, "config_dir", None)
    root_dir = getattr(state, "root", None)
    candidate_roots = []
    if isinstance(config_dir, Path):
        candidate_roots.append(config_dir / "personas")
    if isinstance(root_dir, Path):
        candidate_roots.append(root_dir / "personas")
    for root in candidate_roots:
        persona_path = root / f"{persona_id}.yaml"
        if persona_path.exists():
            return f"pv_{canonical_sha256(persona_path.read_text(encoding='utf-8'))[:16]}"
    return f"pv_{canonical_sha256({'persona_id': persona_id})[:16]}"


def _build_router_artifact(payload: RouterRequest) -> dict:
    text = str(payload.user_input or "").lower()
    allowed = payload.allowed_personas or [
        "research",
        "code_review",
        "implementation",
        "qa_test",
        "release_ops",
        "clarifier",
        "general",
    ]
    preferred = "general"
    reason_tags: list[str] = []
    ask_me_intent = text.startswith("ask me") or "ask me " in text
    if ask_me_intent:
        preferred = "clarifier"
        reason_tags.append("intent_clarification_needed")
    elif any(tok in text for tok in ["research", "find", "source", "docs", "compare"]):
        preferred = "research"
        reason_tags.append("needs_research")
    elif any(tok in text for tok in ["review", "audit", "risk"]):
        preferred = "code_review"
        reason_tags.append("needs_code_review")
    elif any(tok in text for tok in ["test", "qa", "failing", "regression"]):
        preferred = "qa_test"
        reason_tags.append("needs_testing")
    elif any(tok in text for tok in ["release", "deploy", "rollout"]):
        preferred = "release_ops"
        reason_tags.append("needs_release")
    elif any(tok in text for tok in ["implement", "build", "fix", "patch", "code"]):
        preferred = "implementation"
        reason_tags.append("needs_code_edit")
    else:
        reason_tags.append("general_task")
    selected_persona_id = preferred if preferred in allowed else allowed[0]
    now_slice = payload.current_now_slice or {}
    handoff_payload = {
        "user_input": payload.user_input,
        "workspace_refs": payload.workspace_refs,
        "from_now_slice_id": now_slice.get("now_slice_id"),
        "from_persona_id": now_slice.get("persona_id"),
        "constraints": now_slice.get("active_constraints"),
    }
    budgets_by_persona = {
        "research": {"max_tokens": 1800, "max_tool_calls": 6, "max_time_seconds": 900},
        "code_review": {"max_tokens": 2000, "max_tool_calls": 4, "max_time_seconds": 900},
        "implementation": {"max_tokens": 2200, "max_tool_calls": 6, "max_time_seconds": 1200},
        "qa_test": {"max_tokens": 1600, "max_tool_calls": 8, "max_time_seconds": 1200},
        "release_ops": {"max_tokens": 1400, "max_tool_calls": 4, "max_time_seconds": 900},
        "clarifier": {"max_tokens": 600, "max_tool_calls": 0, "max_time_seconds": 300},
        "general": {"max_tokens": 1200, "max_tool_calls": 3, "max_time_seconds": 600},
    }
    success_criteria = {
        "research": ["Cites sources", "Captures constraints"],
        "code_review": ["Findings prioritized", "File/line references included"],
        "implementation": ["Code changes compile", "No policy violations"],
        "qa_test": ["Tests executed", "Failures linked to fix guidance"],
        "release_ops": ["Safe rollout plan", "Rollback noted"],
        "clarifier": ["Outputs one user-specific clarifying question"],
        "general": ["Directly answers request"],
    }.get(selected_persona_id, ["Directly answers request"])
    return {
        "selected_persona_id": selected_persona_id,
        "reason_tags": reason_tags,
        "hand_off_payload": handoff_payload,
        "budget": budgets_by_persona.get(selected_persona_id, budgets_by_persona["general"]),
        "success_criteria": success_criteria,
        "allowed_personas": allowed,
        "created_at": _now_iso(),
    }


def _is_non_actionable_implementation_failure(failures: list[dict[str, Any]]) -> bool:
    details = [str((f or {}).get("detail", "")) for f in failures if isinstance(f, dict)]
    if not details:
        return False
    non_actionable_prefixes = (
        "workflow_missing_required_tool:",
        "workflow_missing_required_file:",
        "tool_policy_violation:domain_not_allowlisted",
        "tool_policy_violation:persona_forbidden_tool",
        "tool_policy_violation:budget_exceeded_",
    )
    return any(any(detail.startswith(prefix) for prefix in non_actionable_prefixes) for detail in details)


def _is_actionable_bug_failure(failures: list[dict[str, Any]]) -> bool:
    actionable_codes = {
        "QA_QUALITY_FAILED",
        "WORKFLOW_TOOL_MISSING",
        "WORKFLOW_FILE_MISSING",
        "RUNTIME_FAILURE",
        "EXECUTION_ERROR",
    }
    actionable_detail_prefixes = (
        "qa_behavior_check_failed:",
        "workflow_missing_required_tool:",
        "workflow_missing_required_file:",
        "runtime_error:",
        "firewall_violation:",
    )
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        code = str(failure.get("code") or "").strip().upper()
        detail = str(failure.get("detail") or "").strip().lower()
        if code in actionable_codes:
            return True
        if any(detail.startswith(prefix) for prefix in actionable_detail_prefixes):
            return True
    return False


def _implementation_repair_instruction_for_target(canonical_target: str | None) -> str:
    target = str(canonical_target or "").strip().lower()
    if target == "ui_page_update":
        return (
            "Implementation output was non-actionable. Use write_file to create/update required UI file inside workspace: "
            "apps/ui/src/main.jsx. Make a concrete, visible page update aligned with the request, preserving the current tech stack."
        )
    if target == "service_bootstrap_app":
        return (
            "Implementation output was non-actionable. Use write_file to create required files inside workspace: "
            "app/main.py, tests/test_main.py, and README.md. Build a minimal FastAPI service with /health and CRUD "
            "endpoints (/items create/read/update/delete) and add behavioral TestClient tests. Do not use absolute host paths."
        )
    if target == "weather_station_app":
        return (
            "Implementation output was non-actionable. Use write_file to create required files inside workspace: "
            "app/main.py and tests/test_weather.py. Implement a minimal FastAPI weather endpoint and a behavioral "
            "TestClient test. Do not use absolute host paths."
        )
    if target == "hello_fastapi_service":
        return (
            "Implementation output was non-actionable. Use write_file to create required files inside workspace: "
            "app/main.py and tests/test_main.py. Implement a minimal FastAPI hello endpoint and a behavioral "
            "TestClient test. Do not use absolute host paths."
        )
    if target == "generic_build_app":
        return (
            "Implementation output was non-actionable. Use write_file to create required files inside workspace: "
            "app/main.py and tests/test_main.py. Build a minimal in-memory CRUD API (create/read/update/delete) "
            "and add behavioral TestClient tests for those endpoints. Do not use absolute host paths."
        )
    return (
        "Implementation output was non-actionable. Use write_file to create required workflow files inside workspace. "
        "Avoid absolute host paths."
    )


def _build_planner_artifact(*, user_input: str, requested_target: Optional[str]) -> dict:
    text = str(user_input or "").strip().lower()
    target = str(requested_target or "").strip().lower()
    reason_tags: list[str] = []
    confidence = 0.55
    if target:
        reason_tags.append("target_from_request")
        confidence = 0.99
    elif any(tok in text for tok in ["weather", "forecast", "/weather"]):
        target = "weather_station_app"
        reason_tags.extend(["intent_weather_service", "target_inferred"])
        confidence = 0.94
    elif any(tok in text for tok in ["crud", "rest", "resource", "/health", "bootstrap"]) and any(
        tok in text for tok in ["app", "service", "api"]
    ):
        target = "service_bootstrap_app"
        reason_tags.extend(["intent_service_bootstrap", "target_inferred"])
        confidence = 0.9
    elif any(tok in text for tok in ["hello"]) and any(tok in text for tok in ["app", "service", "api"]):
        target = "hello_fastapi_service"
        reason_tags.extend(["intent_hello_service", "target_inferred"])
        confidence = 0.92
    elif any(tok in text for tok in ["add", "update", "change", "move", "build", "create", "implement", "make"]) and any(
        tok in text for tok in [
            "widget",
            "ui",
            "page",
            "frontend",
            "tile",
            "title",
            "header",
            "layout",
            "app",
            "service",
            "crud",
            "weather",
            "hello",
        ]
    ):
        target = "ui_page_update"
        reason_tags.extend(["intent_ui_first", "target_inferred"])
        confidence = 0.9
    elif any(tok in text for tok in ["build", "create", "implement", "make", "update", "change"]) and any(
        tok in text for tok in ["app", "service", "page", "ui"]
    ):
        target = "generic_build_app"
        reason_tags.extend(["intent_build", "target_generic"])
        confidence = 0.62
    else:
        target = ""
        reason_tags.append("no_structured_build_target")
        confidence = 0.42

    persona_plan = (
        ["research", "implementation", "code_review", "qa_test", "release_ops"]
        if target in {"hello_fastapi_service", "weather_station_app", "generic_build_app", "service_bootstrap_app", "ui_page_update"}
        else []
    )
    success_criteria = {
        "service_bootstrap_app": [
            "writes service code",
            "writes tests",
            "tests pass",
            "starts service",
            "serves /health",
            "serves CRUD endpoints",
        ],
        "hello_fastapi_service": ["writes app code", "writes tests", "starts service", "serves /hello"],
        "weather_station_app": ["writes app code", "writes tests", "starts service", "serves /weather"],
        "generic_build_app": ["writes app code", "writes tests", "provides run steps"],
        "ui_page_update": ["updates UI code", "keeps current stack", "provides run steps"],
    }.get(target, ["directly answer request"])
    components = {
        "service_bootstrap_app": [
            {"id": "api", "name": "Service API", "required": True, "details": "FastAPI app with /health and CRUD endpoints"},
            {"id": "tests", "name": "Tests", "required": True, "details": "Behavioral TestClient tests for health + CRUD"},
            {"id": "runbook", "name": "Run Instructions", "required": True, "details": "Install/run/test/curl validation steps"},
        ],
        "hello_fastapi_service": [
            {"id": "api", "name": "HTTP API", "required": True, "details": "FastAPI app with /hello endpoint"},
            {"id": "tests", "name": "Tests", "required": True, "details": "Route-level tests for /hello"},
            {"id": "runbook", "name": "Run Instructions", "required": True, "details": "Install/run/curl validation steps"},
        ],
        "weather_station_app": [
            {"id": "api", "name": "Weather API", "required": True, "details": "Service endpoint for weather data"},
            {"id": "data_source", "name": "Weather Data Source", "required": True, "details": "Stub or live provider wiring"},
            {"id": "tests", "name": "Tests", "required": True, "details": "Endpoint + payload checks"},
            {"id": "runbook", "name": "Run Instructions", "required": True, "details": "Local run + curl verification"},
        ],
        "generic_build_app": [
            {"id": "app", "name": "App Core", "required": True, "details": "Minimal runnable app/service"},
            {"id": "tests", "name": "Tests", "required": True, "details": "At least one regression check"},
            {"id": "runbook", "name": "Run Instructions", "required": True, "details": "Build/run/test commands"},
        ],
        "ui_page_update": [
            {"id": "ui", "name": "UI Surface", "required": True, "details": "Update existing React page structure"},
            {"id": "style", "name": "Visual Style", "required": False, "details": "Adjust styles only as needed"},
            {"id": "runbook", "name": "Run Instructions", "required": True, "details": "How to verify UI changes locally"},
        ],
    }.get(target, [])
    assumptions = {
        "service_bootstrap_app": [
            "Python + FastAPI stack is acceptable unless explicitly overridden.",
            "Data can be in-memory for v1 if persistence is not required.",
            "Backend-first delivery is acceptable unless a full frontend is explicitly requested.",
        ],
        "hello_fastapi_service": [
            "Python + FastAPI stack is acceptable.",
            "Service can bind to localhost and expose one endpoint.",
        ],
        "weather_station_app": [
            "Initial version may use stubbed weather data unless API credentials are provided.",
            "Single-city query endpoint is sufficient for first milestone.",
            "Backend-only implementation is acceptable unless UI is explicitly requested.",
        ],
        "generic_build_app": [
            "Default to minimal, testable implementation with local run instructions.",
        ],
        "ui_page_update": [
            "Existing React + Vite frontend should be preserved.",
            "Changes should be localized to the current page structure unless requested otherwise.",
        ],
    }.get(target, [])
    confirmations_needed = {
        "service_bootstrap_app": [
            "Confirm whether persistent storage is required for CRUD (or in-memory is acceptable for v1).",
            "Confirm whether UI should be generated now or API-first is sufficient.",
            "Confirm preferred deployment target (local-only vs container/cloud).",
        ],
        "hello_fastapi_service": [
            "Confirm preferred framework if not FastAPI.",
        ],
        "weather_station_app": [
            "Confirm live weather provider (or approve stubbed data for v1).",
            "Confirm whether a frontend dashboard is required now or backend-only is acceptable.",
            "Confirm deployment target (local-only vs cloud runtime).",
        ],
        "generic_build_app": [
            "Confirm preferred language/framework and runtime constraints.",
        ],
        "ui_page_update": [],
    }.get(target, [])
    plan_steps = {
        "service_bootstrap_app": [
            "Research stack constraints and delivery scope (API-only vs API+UI).",
            "Implement FastAPI service with /health and CRUD endpoints.",
            "Run code review and capture risks before test execution.",
            "Add behavioral tests and execute them.",
            "Publish runbook/release steps and open assumptions for unresolved scope.",
        ],
        "hello_fastapi_service": [
            "Research minimal FastAPI service conventions.",
            "Implement service files and endpoint.",
            "Run code review and capture risks before test execution.",
            "Add tests and execute them.",
            "Publish runbook and release checklist.",
        ],
        "weather_station_app": [
            "Research weather app requirements and provider constraints.",
            "Implement API endpoint and data-source abstraction.",
            "Run code review and capture risks before test execution.",
            "Add tests for endpoint contract and run them.",
            "Publish runbook/release steps and open assumptions.",
        ],
        "generic_build_app": [
            "Clarify target and constraints.",
            "Implement minimal viable service/app.",
            "Run code review and capture risks before test execution.",
            "Add tests and run validation.",
            "Publish run steps and follow-up confirmations.",
        ],
        "ui_page_update": [
            "Inspect current UI structure and identify update location.",
            "Implement requested page/widget update in current stack.",
            "Run code review and capture risks before test execution.",
            "Run frontend build/test checks.",
            "Publish verification steps and any follow-up assumptions.",
        ],
    }.get(target, ["Ask clarifying question and gather missing constraints."])
    risk_flags = []
    if target == "service_bootstrap_app":
        risk_flags.extend(["ui_scope_unclear", "persistence_scope_unclear"])
    if target == "weather_station_app":
        risk_flags.extend(["provider_api_key_missing", "unclear_ui_scope"])
    if not target:
        risk_flags.append("no_explicit_target")

    return {
        "planner_id": f"pln_{uuid.uuid4().hex[:12]}",
        "intent_summary": str(user_input or "").strip(),
        "canonical_target": target or None,
        "persona_plan": persona_plan,
        "reason_tags": reason_tags,
        "components": components,
        "assumptions": assumptions,
        "confirmations_needed": confirmations_needed,
        "plan_steps": plan_steps,
        "risk_flags": risk_flags,
        "success_criteria": success_criteria,
        "confidence": float(confidence),
        "created_at": _now_iso(),
    }


def _build_clarification_questions(*, planner_artifact: dict) -> list[dict[str, Any]]:
    confirmations = [str(x).strip() for x in (planner_artifact.get("confirmations_needed") or []) if str(x).strip()]
    questions: list[dict[str, Any]] = []
    for idx, question in enumerate(confirmations[:3], start=1):
        questions.append(
            {
                "id": f"confirm_{idx}",
                "question": question,
                "required": True,
            }
        )
    return questions


def _answers_text(answers: dict[str, str]) -> str:
    items = [(str(k).strip(), str(v).strip()) for k, v in answers.items() if str(v).strip()]
    if not items:
        return ""
    lines = ["Clarifications from user:"]
    for key, value in sorted(items):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _persist_planner_artifact(*, planner_artifact: dict, state: AppState) -> tuple[str, str]:
    artifact_id, artifact_path = state.router_artifact_store.put_json(planner_artifact)
    state.db.insert_artifact_registry(
        artifact_id=artifact_id,
        created_at=_now_iso(),
        artifact_type="planner_artifact",
        content_hash=canonical_sha256(planner_artifact),
        artifact_path=artifact_path,
        metadata_json=json.dumps(
            {
                "canonical_target": planner_artifact.get("canonical_target"),
                "confidence": planner_artifact.get("confidence"),
            }
        ),
    )
    return artifact_id, artifact_path


def _build_decomposition_plan(*, planner_artifact: dict) -> dict:
    components = planner_artifact.get("components") or []
    plan_steps = planner_artifact.get("plan_steps") or []
    success_criteria = planner_artifact.get("success_criteria") or []
    tasks: list[dict[str, Any]] = []
    for idx, step in enumerate(plan_steps, start=1):
        tasks.append(
            {
                "task_id": f"t{idx}",
                "title": str(step),
                "depends_on": [f"t{idx-1}"] if idx > 1 else [],
            }
        )
    return {
        "decomposition_id": f"decomp_{uuid.uuid4().hex[:12]}",
        "canonical_target": planner_artifact.get("canonical_target"),
        "components": components,
        "tasks": tasks,
        "success_criteria": success_criteria,
        "created_at": _now_iso(),
    }


def _persist_decomposition_plan(*, decomposition_plan: dict, state: AppState) -> tuple[str, str]:
    artifact_id, artifact_path = state.router_artifact_store.put_json(decomposition_plan)
    state.db.insert_artifact_registry(
        artifact_id=artifact_id,
        created_at=_now_iso(),
        artifact_type="decomposition_plan",
        content_hash=canonical_sha256(decomposition_plan),
        artifact_path=artifact_path,
        metadata_json=json.dumps(
            {
                "canonical_target": decomposition_plan.get("canonical_target"),
                "task_count": len(decomposition_plan.get("tasks") or []),
            }
        ),
    )
    return artifact_id, artifact_path


def _persona_objective(persona_id: str, canonical_target: str) -> str:
    objective_by_persona = {
        "research": "Collect grounded constraints and implementation references for this target.",
        "code_review": "Evaluate proposed changes for risk and correctness before execution.",
        "implementation": "Create or modify workspace files to satisfy required functionality.",
        "qa_test": "Execute tests and report actionable failures with direct repair signals.",
        "release_ops": "Produce run/release steps and final handoff summary.",
        "clarifier": "Ask one high-value clarifying question to reduce ambiguity.",
    }
    objective = objective_by_persona.get(persona_id, "Advance the current step toward completion.")
    if canonical_target:
        return f"{objective} Target={canonical_target}."
    return objective


def _persona_acceptance_checks(persona_id: str, canonical_target: str) -> list[str]:
    target_checks = {
        "service_bootstrap_app": [
            "app/main.py exists",
            "tests/test_main.py exists",
            "README.md exists",
            "tests pass for /health and CRUD endpoints",
        ],
        "hello_fastapi_service": ["app/main.py exists", "tests/test_main.py exists", "tests pass for /hello"],
        "weather_station_app": ["app/main.py exists", "tests/test_weather.py exists", "tests pass for /weather"],
        "generic_build_app": ["app/main.py exists", "tests/test_main.py exists", "CRUD behavior tests pass"],
        "ui_page_update": ["apps/ui/src/main.jsx updated", "UI change is visible and request-aligned"],
    }.get(canonical_target, [])
    by_persona = {
        "research": ["Includes citations or source refs", "Captures constraints and assumptions"],
        "code_review": ["Identifies concrete risks", "Provides actionable remediation notes"],
        "implementation": ["Uses required tool calls", "Writes only workspace-scoped files"],
        "qa_test": ["Runs canonical pytest command", "Returns actionable failure summary if failing"],
        "release_ops": ["Includes run commands", "Includes verification command(s)"],
        "clarifier": ["Single concrete clarification question"],
    }.get(persona_id, ["Progresses current step with explicit outputs"])
    return by_persona + target_checks


def _persona_handoff_requirements(persona_id: str) -> list[str]:
    if persona_id == "research":
        return ["evidence_summary", "constraints", "source_refs"]
    if persona_id == "code_review":
        return ["findings", "risk_assessment", "recommended_fixes"]
    if persona_id == "implementation":
        return ["changed_files", "test_expectations", "known_gaps"]
    if persona_id == "qa_test":
        return ["test_results", "failure_summary", "repair_suggestions"]
    if persona_id == "release_ops":
        return ["runbook", "verification_steps", "rollback_note"]
    if persona_id == "clarifier":
        return ["clarifying_question"]
    return ["summary", "artifacts"]


def _build_persona_thread_projection(
    *,
    planner_artifact: dict,
    decomposition_plan: dict,
    planner_artifact_id: str,
    decomposition_plan_artifact_id: str,
    canonical_target: str,
    persona_id: str,
    step_index: int,
    required_tools: list[str],
    handoff_artifact_id: Optional[str],
    workspace_refs: list[str],
    workspace_id: Optional[str],
    request_id: str,
) -> dict[str, Any]:
    tasks = decomposition_plan.get("tasks") or []
    current_step = tasks[step_index] if step_index < len(tasks) and isinstance(tasks[step_index], dict) else None
    next_steps: list[str] = []
    for idx in range(step_index + 1, min(step_index + 3, len(tasks))):
        t = tasks[idx]
        if isinstance(t, dict) and t.get("task_id"):
            next_steps.append(str(t.get("task_id")))
    input_artifacts = {
        "planner_artifact_id": planner_artifact_id,
        "decomposition_plan_artifact_id": decomposition_plan_artifact_id,
        "handoff_artifact_id": handoff_artifact_id,
        "workspace_refs": workspace_refs,
    }
    workspace_container = {
        "container_id": (f"workspace://{workspace_id}" if workspace_id else f"workspace://request/{request_id}"),
        "workspace_id": workspace_id,
        "root": (f"data/workspaces/{workspace_id}" if workspace_id else "data/workspaces"),
        "writable_prefixes": ([f"data/workspaces/{workspace_id}/"] if workspace_id else ["data/workspaces/"]),
    }
    world_projection = {
        "runtime": "docker_compose",
        "ui_page": {
            "url": "http://127.0.0.1:8000",
            "tech_stack": ["react", "vite", "fastapi"],
            "source_files": ["apps/ui/src/main.jsx", "apps/ui/styles.css", "apps/ui/index.html"],
        },
        "deployment_target": {
            "type": "workspace_hello_service",
            "trigger": "post_workflow_if_build_target",
            "health_path": "/health",
            "url_template": "http://127.0.0.1:{allocated_port}",
        },
        "workspace_container": workspace_container,
    }
    return {
        "version": "wp_thread_v1",
        "goal": {
            "target_id": canonical_target or None,
            "done_definition": planner_artifact.get("success_criteria") or [],
        },
        "scope": {
            "components": planner_artifact.get("components") or [],
            "assumptions": planner_artifact.get("assumptions") or [],
            "confirmations_needed": planner_artifact.get("confirmations_needed") or [],
        },
        "execution": {
            "current_persona": persona_id,
            "persona_mission": _persona_objective(persona_id, canonical_target),
            "current_step_index": step_index,
            "current_step": current_step,
            "next_steps": next_steps,
        },
        "constraints": {
            "required_tools": required_tools,
            "budgets": {"max_time_seconds": 1200, "max_tool_calls": 8},
        },
        "quality": {
            "acceptance_checks": _persona_acceptance_checks(persona_id, canonical_target),
            "handoff_requirements": _persona_handoff_requirements(persona_id),
            "risk_flags": planner_artifact.get("risk_flags") or [],
        },
        "world": world_projection,
        "traceability": input_artifacts,
    }


def _materialize_generated_pod(*, variant: Any, state: AppState) -> None:
    if variant.pod_id not in state.pods:
        persona = str((variant.config or {}).get("persona", "general"))
        state.pods[variant.pod_id] = Pod.create(
            variant.pod_id,
            state.db,
            ArtifactStore(state.data_dir / "artifacts" / variant.pod_id),
            persona=persona,
            config_dir=getattr(state, "config_dir", None),
        )
    if variant.pod_id not in state.router.pod_ids:
        state.router.pod_ids.append(variant.pod_id)
    state.router.update_weight(variant.pod_id, 0.5)


def _persist_router_artifact(*, router_artifact: dict, state: AppState) -> tuple[str, str]:
    artifact_id, artifact_path = state.router_artifact_store.put_json(router_artifact)
    state.db.insert_artifact_registry(
        artifact_id=artifact_id,
        created_at=_now_iso(),
        artifact_type="router_artifact",
        content_hash=canonical_sha256(router_artifact),
        artifact_path=artifact_path,
        metadata_json=json.dumps({"selected_persona_id": router_artifact.get("selected_persona_id")}),
    )
    return artifact_id, artifact_path


def _search_local_kb(*, state: AppState, query: str, max_hits: int) -> list[dict]:
    q = query.strip().lower()
    if not q:
        return []
    hits: list[dict] = []
    kb_root = state.data_dir / "kb"
    if kb_root.exists():
        for p in sorted(kb_root.rglob("*")):
            if len(hits) >= max_hits:
                break
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for ln, line in enumerate(text.splitlines(), start=1):
                if q in line.lower():
                    hits.append({"source": str(p), "line_start": ln, "line_end": ln, "snippet": line[:240]})
                    if len(hits) >= max_hits:
                        break
    if len(hits) >= max_hits:
        return hits
    zip_path = state.data_dir / "artifacts.zip"
    if zip_path.exists():
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if len(hits) >= max_hits:
                        break
                    if info.is_dir():
                        continue
                    if not re.search(r"\.(txt|md|json|yaml|yml|py|js|ts|log)$", info.filename, flags=re.IGNORECASE):
                        continue
                    try:
                        text = zf.read(info.filename).decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    for ln, line in enumerate(text.splitlines(), start=1):
                        if q in line.lower():
                            hits.append(
                                {
                                    "source": f"{zip_path}:{info.filename}",
                                    "line_start": ln,
                                    "line_end": ln,
                                    "snippet": line[:240],
                                }
                            )
                            if len(hits) >= max_hits:
                                break
        except Exception:
            return hits
    return hits


def _apply_commit_to_registry(*, state: AppState, target: str, changes: dict[str, Any], commit_id: str) -> dict[str, Any]:
    registry_root = (state.data_dir / "registry").resolve()
    registry_root.mkdir(parents=True, exist_ok=True)
    versions_path = registry_root / "versions.json"
    if versions_path.exists():
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
    else:
        versions = {}
    current = int(versions.get(target, 0))
    next_version = current + 1
    versions[target] = next_version
    versions_path.write_text(json.dumps(versions, indent=2, sort_keys=True), encoding="utf-8")

    version_root = registry_root / target / f"v{next_version:04d}"
    version_root.mkdir(parents=True, exist_ok=True)
    applied_files: list[str] = []

    def _write_rel(rel_path: str, content: str) -> None:
        rel = str(rel_path).strip().lstrip("/")
        if not rel:
            return
        out = (version_root / rel).resolve()
        if version_root != out and version_root not in out.parents:
            raise HTTPException(status_code=400, detail=f"invalid registry path: {rel_path}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        applied_files.append(str(out))

    files = changes.get("files")
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            _write_rel(str(item.get("path", "")), str(item.get("content", "")))
    elif "path" in changes and "content" in changes:
        _write_rel(str(changes.get("path", "")), str(changes.get("content", "")))
    else:
        fallback = {"commit_id": commit_id, "target": target, "changes": changes}
        _write_rel("changes.json", json.dumps(fallback, indent=2, sort_keys=True))

    meta = {
        "commit_id": commit_id,
        "target": target,
        "version": next_version,
        "applied_files": applied_files,
        "applied_at": _now_iso(),
    }
    (version_root / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    applied_files.append(str((version_root / "meta.json").resolve()))
    return {
        "target": target,
        "version": next_version,
        "version_root": str(version_root),
        "applied_files": applied_files,
        "versions_path": str(versions_path),
    }


def _build_auto_commit_changes(
    *,
    state: AppState,
    canonical_target: str,
    workspace_id: str,
    run_ids: list[str],
) -> dict[str, Any] | None:
    files: list[dict[str, str]] = []
    if run_ids:
        placeholders = ",".join(["?"] * len(run_ids))
        tool_rows = state.db.fetchall(
            f"SELECT result_artifact_path FROM tool_calls WHERE run_id IN ({placeholders}) AND tool = 'write_file' AND allowed = 1 ORDER BY created_at",
            tuple(run_ids),
        )
        seen_paths: set[str] = set()
        for row in tool_rows:
            result_path = str(row["result_artifact_path"] or "")
            if not result_path:
                continue
            payload = _read_json_from_data_path(state, result_path)
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                continue
            src_path = str(result.get("path") or "").strip()
            if not src_path or src_path in seen_paths:
                continue
            p = Path(src_path).resolve()
            if not p.exists() or not p.is_file():
                continue
            seen_paths.add(src_path)
            rel = p.name
            if "/tests/" in src_path:
                rel = f"tests/{p.name}"
            elif "/app/" in src_path:
                rel = f"app/{p.name}"
            elif p.name.lower() == "readme.md":
                rel = "README.md"
            content = p.read_text(encoding="utf-8", errors="replace")
            files.append({"path": f"{canonical_target}/{rel}", "content": content[:50_000]})
    if not files:
        root = (state.data_dir / "workspaces" / workspace_id).resolve()
        if root.exists():
            selected: list[str] = []
            for rel in ("app/main.py", "README.md"):
                p = (root / rel).resolve()
                if p.exists() and p.is_file():
                    selected.append(rel)
            tests_dir = (root / "tests").resolve()
            if tests_dir.exists() and tests_dir.is_dir():
                for tp in sorted(tests_dir.glob("test_*.py")):
                    if tp.is_file():
                        selected.append(str(tp.relative_to(root)))
            for rel in selected[:6]:
                p = (root / rel).resolve()
                content = p.read_text(encoding="utf-8", errors="replace")
                files.append({"path": f"{canonical_target}/{rel}", "content": content[:50_000]})
    if not files:
        return None
    return {"source": {"workspace_id": workspace_id, "canonical_target": canonical_target}, "files": files}


def _run_auto_commit(
    *,
    state: AppState,
    run_id: str,
    workflow_id: str,
    canonical_target: str,
    workspace_id: str,
    planner_artifact_id: str,
    workflow_graph_artifact_id: str,
    run_ids: list[str],
) -> dict[str, Any]:
    changes = _build_auto_commit_changes(
        state=state,
        canonical_target=canonical_target,
        workspace_id=workspace_id,
        run_ids=run_ids,
    )
    if changes is None:
        return {"attempted": False, "reason": "no_workspace_changes_detected"}
    proposal = {
        "proposal_id": f"pc_{uuid.uuid4().hex[:12]}",
        "run_id": run_id,
        "target": "tests",
        "changes": changes,
        "summary": f"Auto-commit from workflow {workflow_id} ({canonical_target})",
        "created_at": _now_iso(),
        "lineage": {
            "workflow_id": workflow_id,
            "planner_artifact_id": planner_artifact_id,
            "workflow_graph_artifact_id": workflow_graph_artifact_id,
        },
    }
    proposal_artifact_id, proposal_artifact_path = state.router_artifact_store.put_json(proposal)
    state.db.insert_artifact_registry(
        artifact_id=proposal_artifact_id,
        created_at=_now_iso(),
        artifact_type="proposed_commit",
        content_hash=canonical_sha256(proposal),
        artifact_path=proposal_artifact_path,
        metadata_json=json.dumps({"target": "tests", "run_id": run_id, "workflow_id": workflow_id}),
    )
    proposal_text = json.dumps(proposal, sort_keys=True)
    failures: list[str] = []
    violation = detect_survival_awareness_violation(proposal_text)
    if violation is not None:
        failures.append("survival_awareness_leakage")
    lowered = proposal_text.lower()
    if any(tok in lowered for tok in ["evaluator gaming", "judge gaming", "hack the judge", "reward hack"]):
        failures.append("evaluator_gaming_risk")
    if failures:
        return {
            "attempted": True,
            "proposal_artifact_id": proposal_artifact_id,
            "pass": False,
            "failures": failures,
        }
    commit_payload = {
        "commit_id": f"cmt_{uuid.uuid4().hex[:12]}",
        "proposal_artifact_id": proposal_artifact_id,
        "run_id": run_id,
        "target": "tests",
        "changes": changes,
        "summary": proposal["summary"],
        "approved_by": "curator_v1",
        "created_at": _now_iso(),
    }
    registry_apply = _apply_commit_to_registry(
        state=state,
        target="tests",
        changes=changes,
        commit_id=str(commit_payload["commit_id"]),
    )
    commit_payload["registry"] = registry_apply
    commit_artifact_id, commit_artifact_path = state.router_artifact_store.put_json(commit_payload)
    state.db.insert_artifact_registry(
        artifact_id=commit_artifact_id,
        created_at=_now_iso(),
        artifact_type="commit",
        content_hash=canonical_sha256(commit_payload),
        artifact_path=commit_artifact_path,
        metadata_json=json.dumps({"target": "tests", "run_id": run_id, "workflow_id": workflow_id}),
    )
    return {
        "attempted": True,
        "proposal_artifact_id": proposal_artifact_id,
        "pass": True,
        "commit_artifact_id": commit_artifact_id,
        "commit_artifact_path": commit_artifact_path,
        "registry": registry_apply,
    }


def _sanitize_learning_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"(?i)\bapi[_\s-]?key\b", "[redacted]", cleaned)
    cleaned = re.sub(r"(?i)\bpassword\b", "[redacted]", cleaned)
    cleaned = re.sub(r"(?i)\bprivate[_\s-]?key\b", "[redacted]", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _looks_like_schema_noise(text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    markers = ['"response"', '"tool_calls"', '"structured"', "```json", "executor_output_schema"]
    return any(m in lowered for m in markers)


def _stack_mismatch_reason(*, user_input: str, summary: str) -> str | None:
    req = str(user_input or "").lower()
    out = str(summary or "").lower()
    wants_node = any(tok in req for tok in ["node", "express", "javascript", "json data store"])
    wants_python = any(tok in req for tok in ["python", "fastapi", "flask"])
    mentions_node = any(tok in out for tok in ["node", "express", "javascript"])
    mentions_python = any(tok in out for tok in ["python", "fastapi", "flask"])
    if wants_node and mentions_python and not mentions_node:
        return "stack_mismatch_requested_node_got_python"
    if wants_python and mentions_node and not mentions_python:
        return "stack_mismatch_requested_python_got_node"
    return None


def _learning_reject_reasons(
    *,
    user_input: str,
    summary: str,
    extracted_facts: list[str],
) -> list[str]:
    reasons: list[str] = []
    if _looks_like_schema_noise(summary):
        reasons.append("summary_schema_noise")
    mismatch = _stack_mismatch_reason(user_input=user_input, summary=summary)
    if mismatch:
        reasons.append(mismatch)
    facts_joined = " ".join(str(x) for x in extracted_facts).lower()
    if not any(tok in facts_joined for tok in ["tool_call=", "workflow_target=", "persona="]):
        reasons.append("insufficient_actionable_facts")
    return reasons


def _normalize_learning_summary(
    *,
    persona_id: str,
    user_input: str,
    summary: str,
    executor_payload: dict[str, Any] | None,
    extracted_facts: list[str],
) -> tuple[str, list[str]]:
    normalized_summary = str(summary or "").strip()
    normalized_facts = list(extracted_facts)
    payload = executor_payload if isinstance(executor_payload, dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    tool_calls = payload.get("tool_calls") if isinstance(payload.get("tool_calls"), list) else []
    content = str(response.get("content") or "").strip()
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    trace_summary = str(trace.get("summary") or "").strip()

    if persona_id in {"research", "implementation"}:
        compact_parts: list[str] = []
        if content:
            compact_parts.append(content[:220])
        elif trace_summary:
            compact_parts.append(trace_summary[:180])
        compact_parts.append(f"Intent: {user_input[:120]}")
        compact_parts.append(f"Persona: {persona_id}")
        if tool_calls:
            tool_names = [str(call.get("tool") or "").strip() for call in tool_calls if isinstance(call, dict)]
            tool_names = [name for name in tool_names if name][:3]
            if tool_names:
                compact_parts.append("Tools: " + ", ".join(tool_names))
                normalized_facts.extend([f"tool_call={name}" for name in tool_names if f"tool_call={name}" not in normalized_facts])
        normalized_summary = ". ".join(part for part in compact_parts if part).strip()

    if _looks_like_schema_noise(normalized_summary) and content:
        normalized_summary = content[:320]
    return _sanitize_learning_text(normalized_summary), normalized_facts


def _extract_first_json_dict(text: str) -> dict[str, Any] | None:
    content = str(text or "").strip()
    if not content:
        return None
    candidates: list[str] = []
    if content.startswith("{") and content.endswith("}"):
        candidates.append(content)
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content, flags=re.IGNORECASE)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    first = content.find("{")
    last = content.rfind("}")
    if first != -1 and last > first:
        candidates.append(content[first : last + 1].strip())
    for c in candidates:
        try:
            parsed = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _model_curate_learning_entry(
    *,
    user_input: str,
    canonical_target: str,
    persona_id: str,
    now_slice_projection: dict[str, Any] | None,
    raw_summary: str,
    extracted_facts: list[str],
) -> dict[str, Any] | None:
    provider = str(os.getenv("MODEL_PROVIDER", "ollama")).strip().lower()
    model_name = str(os.getenv("MODEL_NAME", "gpt-4o-mini")).strip()
    base_url = str(os.getenv("MODEL_BASE_URL", "http://127.0.0.1:11434")).strip().rstrip("/")
    timeout_s = float(os.getenv("LEARNING_CURATOR_TIMEOUT_SECONDS", "12"))
    prompt = (
        "You are a strict learning curator. Return ONLY JSON object with keys: "
        "pass(boolean), summary(string), facts(array of short strings), guidance(array of short strings), reject_reasons(array). "
        "Keep summary <= 320 chars. Avoid schema echoes and framework drift. "
        f"USER_INPUT: {user_input}\n"
        f"TARGET: {canonical_target}\n"
        f"PERSONA: {persona_id}\n"
        f"NOW_SLICE_PROJECTION: {json.dumps(now_slice_projection or {}, sort_keys=True)}\n"
        f"OUTCOME_SUMMARY: {raw_summary}\n"
        f"OUTCOME_FACTS: {json.dumps(extracted_facts)}\n"
    )
    try:
        if provider in {"openai", "chatgpt", "remote", "abacus"}:
            endpoint = f"{base_url or 'https://api.openai.com/v1'}/chat/completions"
            body = json.dumps(
                {
                    "model": model_name or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 350,
                }
            ).encode("utf-8")
            api_key = str(
                os.getenv("OPENAI_API_KEY", "")
                or os.getenv("ABACUS_API_KEY", "")
                or os.getenv("REMOTE_API_KEY", "")
            )
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
            choices = payload.get("choices") or []
            text = str(((choices[0] or {}).get("message") or {}).get("content") or "")
            parsed = _extract_first_json_dict(text)
            return parsed
        if provider == "llamacpp":
            endpoint = f"{base_url}/completion"
            body = json.dumps({"prompt": prompt, "n_predict": 350, "temperature": 0.1}).encode("utf-8")
            req = urllib_request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib_request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
            return _extract_first_json_dict(str(payload.get("content") or ""))
        endpoint = f"{base_url}/api/generate"
        body = json.dumps(
            {"model": model_name or "llama3.1:8b", "prompt": prompt, "stream": False, "options": {"temperature": 0.1, "num_predict": 350}}
        ).encode("utf-8")
        req = urllib_request.Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        return _extract_first_json_dict(str(payload.get("response") or ""))
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None


def _run_recursive_learning_curator(
    *,
    state: AppState,
    request_id: str,
    user_input: str,
    canonical_target: str | None,
    workflow_steps: list[dict[str, Any]],
    planner_artifact_id: str,
    workflow_graph_artifact_id: str,
    auto_commit_result: dict[str, Any] | None,
) -> dict[str, Any]:
    successful_steps = [s for s in workflow_steps if bool(s.get("pass")) and s.get("attempt_id")]
    if not successful_steps:
        return {"attempted": False, "reason": "no_successful_steps"}
    base_source_ids = [planner_artifact_id, workflow_graph_artifact_id]
    if auto_commit_result:
        pid = str(auto_commit_result.get("proposal_artifact_id") or "").strip()
        cid = str(auto_commit_result.get("commit_artifact_id") or "").strip()
        if pid:
            base_source_ids.append(pid)
        if cid:
            base_source_ids.append(cid)
    base_source_ids = sorted({x for x in base_source_ids if x})
    target_slug = str(canonical_target or "general").strip().lower() or "general"
    items: list[dict[str, Any]] = []
    committed = 0
    for step in successful_steps:
        attempt_id = str(step.get("attempt_id") or "")
        run_id = str(step.get("run_id") or "")
        persona_id = str(step.get("persona_id") or "general")
        attempt_row = state.db.get_run_attempt(attempt_id)
        if attempt_row is None:
            continue
        summary = f"Request: {user_input}. Persona: {persona_id}. "
        extracted_facts: list[str] = [
            f"workflow_target={target_slug}",
            f"persona={persona_id}",
            f"run_id={run_id}",
            f"attempt_id={attempt_id}",
        ]
        now_slice_projection: dict[str, Any] | None = None
        try:
            now_row = state.db.fetchone(
                """
                SELECT ns.artifact_path AS now_slice_path
                FROM runs r
                JOIN now_slices ns ON ns.now_slice_id = r.now_slice_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            )
            if now_row is not None and now_row["now_slice_path"]:
                now_payload = _read_json_from_data_path(state, str(now_row["now_slice_path"]))
                wp = now_payload.get("workflow_projection") if isinstance(now_payload, dict) else None
                if isinstance(wp, dict):
                    now_slice_projection = wp
        except Exception:
            now_slice_projection = None
        artifact_executor_path = str(attempt_row["artifact_executor_path"] or "")
        executor_payload: dict[str, Any] | None = None
        if artifact_executor_path:
            try:
                executor_payload = _read_json_from_data_path(state, artifact_executor_path)
                content = str(((executor_payload.get("response") or {}).get("content")) or "").strip()
                if content:
                    summary += content[:700]
                tool_calls = executor_payload.get("tool_calls") or []
                if isinstance(tool_calls, list):
                    for call in tool_calls[:6]:
                        if not isinstance(call, dict):
                            continue
                        tool = str(call.get("tool") or "").strip()
                        if tool:
                            extracted_facts.append(f"tool_call={tool}")
            except Exception:
                pass
        summary, extracted_facts = _normalize_learning_summary(
            persona_id=persona_id,
            user_input=user_input,
            summary=summary,
            executor_payload=executor_payload,
            extracted_facts=extracted_facts,
        )
        reject_reasons = _learning_reject_reasons(user_input=user_input, summary=summary, extracted_facts=extracted_facts)
        curation_model = _model_curate_learning_entry(
            user_input=user_input,
            canonical_target=target_slug,
            persona_id=persona_id,
            now_slice_projection=now_slice_projection,
            raw_summary=summary,
            extracted_facts=extracted_facts,
        )
        if isinstance(curation_model, dict):
            model_rejects = [str(x) for x in (curation_model.get("reject_reasons") or []) if str(x).strip()]
            reject_reasons.extend(model_rejects)
            model_pass = bool(curation_model.get("pass", False))
            model_summary = str(curation_model.get("summary") or "").strip()
            model_facts = [str(x).strip() for x in (curation_model.get("facts") or []) if str(x).strip()]
            model_guidance = [str(x).strip() for x in (curation_model.get("guidance") or []) if str(x).strip()]
            if model_summary:
                summary = model_summary
            if model_facts:
                extracted_facts.extend(model_facts[:8])
            if model_guidance:
                extracted_facts.extend([f"guidance={g}" for g in model_guidance[:4]])
            if not model_pass and not model_rejects:
                reject_reasons.append("model_curator_rejected")
        reject_reasons = sorted({r for r in reject_reasons if r})
        if reject_reasons:
            items.append(
                {
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "persona_id": persona_id,
                    "doc_key": f"workflow/{target_slug}/{persona_id}",
                    "result": {"pass": False, "reason": "strict_filter_rejected", "reject_reasons": reject_reasons},
                    "curation_rounds": [],
                }
            )
            continue
        summary = _sanitize_learning_text(summary)[:1200]
        extracted_facts = [_sanitize_learning_text(f)[:220] for f in extracted_facts if str(f).strip()]
        doc_key = f"workflow/{target_slug}/{persona_id}"
        lease = state.workspace.create_lease(
            run_id=run_id,
            attempt_id=attempt_id,
            capabilities=["index"],
            roots=[f"workspace/{run_id}/{attempt_id}/learning"],
            budgets={"max_bytes": 20000, "max_files": 10, "max_ops": 8, "max_time_seconds": 300},
            ttl_seconds=300,
        )
        attempt_logs: list[dict[str, Any]] = []
        commit_result: dict[str, Any] | None = None
        source_ids = list(base_source_ids)
        for round_idx in range(1, 4):
            commit_result = state.workspace.commit_knowledge(
                lease_id=str(lease["lease_id"]),
                doc_key=doc_key,
                title=f"{target_slug}::{persona_id}",
                summary=summary,
                extracted_facts=extracted_facts,
                source_artifact_ids=source_ids,
            )
            gate = commit_result.get("gate") if isinstance(commit_result, dict) else {}
            failures = gate.get("failures") if isinstance(gate, dict) else []
            failures = [str(x) for x in (failures or [])]
            attempt_logs.append(
                {
                    "round": round_idx,
                    "pass": bool(commit_result.get("pass")),
                    "reason": commit_result.get("reason"),
                    "failures": failures,
                }
            )
            if bool(commit_result.get("pass")):
                committed += 1
                break
            if any(f == "duplicate_content" for f in failures):
                break
            if any(f == "missing_source_artifact_ids" for f in failures) and not source_ids:
                source_ids = [planner_artifact_id, workflow_graph_artifact_id]
            if any(f == "not_relevant_to_request" for f in failures):
                summary = _sanitize_learning_text(f"Request: {user_input}. {summary}")[:1200]
                extracted_facts.append(f"request_terms={_sanitize_learning_text(user_input)[:120]}")
            if any(f == "contains_disallowed_content" for f in failures):
                summary = _sanitize_learning_text(summary)
                extracted_facts = [_sanitize_learning_text(x) for x in extracted_facts]
        items.append(
            {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "persona_id": persona_id,
                "doc_key": doc_key,
                "result": commit_result,
                "curation_rounds": attempt_logs,
            }
        )
    return {
        "attempted": True,
        "request_id": request_id,
        "items": items,
        "committed_count": committed,
        "pass": committed > 0,
    }


def _update_routing_weight_from_signals(state: AppState, pod_id: str, request_type: str) -> float:
    completions = state.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="completion")
    retries = state.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="retry")
    returns = state.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="return")
    abandons = state.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="abandon")
    avg_ttr_ms = state.db.avg_signal(pod_id=pod_id, request_type=request_type, signal_type="latency")
    if avg_ttr_ms <= 0:
        # Backward-compatible fallback for older signal name.
        avg_ttr_ms = state.db.avg_signal(pod_id=pod_id, request_type=request_type, signal_type="time_to_resolution_ms")

    attempts = max(1, completions + retries + abandons)
    completion_rate = completions / attempts
    retry_rate = retries / attempts
    return_rate = returns / max(1, completions)
    ttr_ms = avg_ttr_ms if avg_ttr_ms > 0 else 10_000.0

    new_weight = state.router.apply_signal_pressure(
        pod_id=pod_id,
        request_type=request_type,
        completion_rate=completion_rate,
        retry_rate=retry_rate,
        return_rate=return_rate,
        time_to_resolution_ms=ttr_ms,
    )
    state.db.upsert_routing_weight_by_type(
        request_type=request_type,
        pod_id=pod_id,
        updated_at=_now_iso(),
        weight=new_weight,
        metadata_json=json.dumps(
            {
                "completion_rate": completion_rate,
                "retry_rate": retry_rate,
                "return_rate": return_rate,
                "avg_ttr_ms": ttr_ms,
            }
        ),
    )
    return new_weight


@router.post("/requests", response_model=SubmitResponse)
def submit_request(payload: SubmitRequest, state: AppState = Depends(get_state)) -> SubmitResponse:
    created_at = _now_iso()
    request_id = state.new_request_id()
    request_type = classify_request_type(payload.user_input, payload.request_type)
    prior_count = state.db.prior_request_count(user_input=payload.user_input, request_type=request_type)
    state.db.insert_request(
        request_id=request_id,
        created_at=created_at,
        status="running",
        user_input=payload.user_input,
        request_type=request_type,
        constraints_json=None,
    )

    enabled = state.db.fetchall("SELECT pod_id FROM pods WHERE is_enabled = 1 ORDER BY pod_id")
    enabled_pod_ids = {str(r["pod_id"]) for r in enabled}
    allocator = getattr(state, "allocator", None)
    candidate_pods = sorted(enabled_pod_ids)
    if allocator is not None:
        candidate_pods = allocator.eligible_pods(request_type=request_type, pod_ids=candidate_pods)
    state_mode = getattr(state, "routing_mode", "auto")
    route_mode = "broadcast"
    if state_mode == "weighted":
        route_mode = "weighted"
    elif state_mode == "auto":
        completions_for_type = sum(
            state.db.count_signals(pod_id=pod_id, request_type=request_type, signal_type="completion")
            for pod_id in candidate_pods
        )
        route_mode = "weighted" if completions_for_type >= max(1, len(candidate_pods)) else "broadcast"
    decision = state.router.route(request_type, mode=route_mode, pod_ids=candidate_pods)
    shared_snapshot = build_snapshot(
        request_id=request_id,
        user_input=payload.user_input,
        request_type=request_type,
        policies=SnapshotPolicies(
            tool_policy_id="tp_default",
            allowed_tools=["http_get", "fs_read"],
            forbidden_tools=["shell_exec", "fs_write"],
            budgets={"max_total_tool_calls": 5, "max_http_get": 3},
        ),
        context_state={},
    )
    run_results = []
    for pod_id in decision.pod_ids:
        if pod_id not in enabled_pod_ids:
            continue
        pod = state.pods[pod_id]
        try:
            run_results.append(pod.run(shared_snapshot))
        except Exception as exc:
            run_results.append(pod.record_failed_run(shared_snapshot, exc))
        if allocator is not None:
            allocator.mark_dispatch(request_type=request_type, pod_id=pod_id)
    if not run_results:
        raise HTTPException(status_code=503, detail="no enabled pods available")

    best = max(run_results, key=_winner_key)
    state.requests[request_id] = {
        "status": "completed",
        "created_at": created_at,
        "resolved_at": _now_iso(),
        "request_type": request_type,
        "winner_run_id": best["run_id"],
        "chosen_run_id": best["run_id"],
        "chosen_pod_id": best["pod_id"],
        "result": best,
        "runs": [r["run_id"] for r in run_results],
    }
    state.db.update_request_status(request_id=request_id, status="done")
    req_created_at = state.db.fetch_request_created_at(request_id) or created_at
    ttr_ms = (_parse_dt(_now_iso()) - _parse_dt(req_created_at)).total_seconds() * 1000.0
    _record_signal(
        state=state,
        request_id=request_id,
        pod_id=best["pod_id"],
        request_type=request_type,
        signal_type="completion",
        value=1.0,
        metadata={"run_id": best["run_id"]},
    )
    _record_signal(
        state=state,
        request_id=request_id,
        pod_id=best["pod_id"],
        request_type=request_type,
        signal_type="latency",
        value=float(max(ttr_ms, 0.0)),
        metadata={"run_id": best["run_id"]},
    )
    if prior_count > 0:
        _record_signal(
            state=state,
            request_id=request_id,
            pod_id=best["pod_id"],
            request_type=request_type,
            signal_type="retry",
            value=1.0,
            metadata={"detected_from": "same_user_input_and_request_type"},
        )
    _update_routing_weight_from_signals(state, best["pod_id"], request_type)
    return SubmitResponse(request_id=request_id)


@router.get("/requests/{request_id}", response_model=RequestStatus)
def get_request(
    request_id: str,
    include_run_details: bool = False,
    include_attempts: bool = False,
    state: AppState = Depends(get_state),
) -> RequestStatus:
    row = state.db.fetchone("SELECT request_id, status FROM requests WHERE request_id = ?", (request_id,))
    if not row:
        raise HTTPException(status_code=404, detail="request not found")
    req_state = state.requests.get(request_id, {})
    result = _normalize_request_result(state, req_state.get("result"))
    artifacts = (result or {}).get("artifacts", {})
    run_details: list[dict] | None = None
    if include_run_details:
        run_rows = state.db.fetchall(
            """
            SELECT run_id, pod_id, dna_id, status, latency_ms, trace_fp, behavior_fp, tool_seq_fp, now_slice_id,
                   executor_output_id, judge_result_id, attempt_count, winner_attempt_num, winner_attempt_id, repaired,
                   winner_executor_output_artifact_path, winner_judge_result_artifact_path
            FROM runs
            WHERE request_id = ?
            ORDER BY created_at ASC
            """,
            (request_id,),
        )
        details = []
        for run_row in run_rows:
            run_id = str(run_row["run_id"])
            tool_rows = state.db.fetchall(
                """
                SELECT tool_call_id, tool, allowed, blocked_reason, started_at, ended_at, error_type, error_message
                FROM tool_calls
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (run_id,),
            )
            judge_row = state.db.fetchone(
                "SELECT pass, scores_json, tags_json, failures_json FROM judge_results WHERE run_id = ?",
                (run_id,),
            )
            judge_summary = None
            if judge_row is not None:
                judge_summary = {
                    "pass": bool(judge_row["pass"]),
                    "scores": json.loads(str(judge_row["scores_json"] or "{}")),
                    "tags": json.loads(str(judge_row["tags_json"] or "[]")),
                    "failures": json.loads(str(judge_row["failures_json"] or "[]")),
                }
            winner_attempt_row = state.db.get_winner_attempt(run_id)
            winner_attempt = None
            if winner_attempt_row is not None:
                winner_attempt = {
                    "attempt_id": str(winner_attempt_row["attempt_id"]),
                    "attempt_num": int(winner_attempt_row["attempt_num"]),
                    "status": str(winner_attempt_row["status"]),
                    "trace_fp": str(winner_attempt_row["trace_fp"]),
                    "behavior_fp": str(winner_attempt_row["behavior_fp"]),
                }
            attempt_count = int(run_row["attempt_count"] or 1)
            include_attempts_for_run = include_attempts or str(run_row["status"]) != "success" or attempt_count > 1
            attempts_payload = None
            if include_attempts_for_run:
                attempts_payload = []
                for a in state.db.list_run_attempts(run_id):
                    attempt_judge_tags = json.loads(str(a["tags_json"] or "[]"))
                    attempts_payload.append(
                        {
                            "attempt_id": str(a["attempt_id"]),
                            "attempt_num": int(a["attempt_num"]),
                            "status": str(a["status"]),
                            "latency_ms": int(a["latency_ms"]),
                            "trace_fp": str(a["trace_fp"]),
                            "behavior_fp": str(a["behavior_fp"]),
                            "pass": bool(a["pass"]),
                            "scores": json.loads(str(a["scores_json"] or "{}")),
                            "judge_tags": attempt_judge_tags,
                            "failures": json.loads(str(a["failures_json"] or "[]")),
                            "dream_grid_fp": a["dream_grid_fp"],
                            "dream_density": float(a["dream_density"] or 0.0),
                            "dream_entropy": float(a["dream_entropy"] or 0.0),
                            "dream_popcount": int(a["dream_popcount"] or 0),
                            "dream_largest_component": int(a["dream_largest_component"] or 0),
                            "dream_symmetry": float(a["dream_symmetry"] or 0.0),
                            "dream_grid": json.loads(str(a["dream_grid_json"])) if a["dream_grid_json"] else None,
                            "executor_prompt_template_id": a["executor_prompt_template_id"],
                            "executor_prompt_hash": a["executor_prompt_hash"],
                            "judge_prompt_template_id": a["judge_prompt_template_id"],
                            "judge_prompt_hash": a["judge_prompt_hash"],
                            "retry_prompt_template_id": a["retry_prompt_template_id"],
                            "retry_prompt_hash": a["retry_prompt_hash"],
                            "inference_params": json.loads(str(a["inference_params_json"] or "{}")),
                            "token_counts": json.loads(str(a["token_counts_json"] or "{}")),
                            "context_truncated": bool(a["context_truncated"]),
                            "truncation_reason": a["truncation_reason"],
                            "error_id": a["error_id"],
                            "artifact_error_path": _portable_ref_path(state, str(a["artifact_error_path"] or "")),
                            "workspace_ops_count": int(a["workspace_ops_count"] or 0),
                            "bytes_written": int(a["bytes_written"] or 0),
                            "knowledge_reads_count": int(a["knowledge_reads_count"] or 0),
                            "knowledge_commits_count": int(a["knowledge_commits_count"] or 0),
                            "knowledge_commit_attempts": int(a["knowledge_commit_attempts"] or 0),
                            "knowledge_commit_pass_rate": float(a["knowledge_commit_pass_rate"] or 0.0),
                            "source_artifact_ids": json.loads(str(a["source_artifact_ids_json"] or "[]")),
                            "persona_id": str(a["persona_id"] or "general"),
                            "persona_version": str(a["persona_version"] or "pv_unknown"),
                            "handoff_artifact_id": a["handoff_artifact_id"],
                            "artifact_executor_path": _portable_ref_path(state, str(a["artifact_executor_path"] or "")),
                            "artifact_judge_path": _portable_ref_path(state, str(a["artifact_judge_path"] or "")),
                            "executor_output_id": a["executor_output_id"],
                            "judge_result_id": a["judge_result_id"],
                        }
                    )
            details.append(
                {
                    "run_id": run_id,
                    "pod_id": str(run_row["pod_id"]),
                    "dna_id": str(run_row["dna_id"]),
                    "status": str(run_row["status"]),
                    "latency_ms": int(run_row["latency_ms"]),
                    "winner_trace_fp": str(run_row["trace_fp"]),
                    "winner_behavior_fp": str(run_row["behavior_fp"]),
                    "trace_fp": str(run_row["trace_fp"]),
                    "behavior_fp": str(run_row["behavior_fp"]),
                    "tool_seq_fp": run_row["tool_seq_fp"],
                    "executor_output_id": run_row["executor_output_id"],
                    "judge_result_id": run_row["judge_result_id"],
                    "attempt_count": attempt_count,
                    "winner_attempt_num": int(run_row["winner_attempt_num"] or 1),
                    "winner_attempt_id": run_row["winner_attempt_id"],
                    "repaired": bool(run_row["repaired"]),
                    "retried": attempt_count > 1,
                    "now_slice_id": run_row["now_slice_id"],
                    "now_slice_artifact_path": _portable_ref_path(
                        state,
                        state.db.fetch_now_slice_path(str(run_row["now_slice_id"])) if run_row["now_slice_id"] else None,
                    ),
                    "winner_executor_output_artifact_path": _portable_ref_path(
                        state,
                        str(run_row["winner_executor_output_artifact_path"] or ""),
                    ),
                    "winner_judge_result_artifact_path": _portable_ref_path(
                        state,
                        str(run_row["winner_judge_result_artifact_path"] or ""),
                    ),
                    "winner_attempt": winner_attempt,
                    "judge": judge_summary,
                    "tool_calls": [
                        {
                            "tool_call_id": str(tool_row["tool_call_id"]),
                            "tool": str(tool_row["tool"]),
                            "allowed": bool(tool_row["allowed"]),
                            "blocked_reason": tool_row["blocked_reason"],
                            "started_at": tool_row["started_at"],
                            "ended_at": tool_row["ended_at"],
                            "error_type": tool_row["error_type"],
                            "error_message": tool_row["error_message"],
                        }
                        for tool_row in tool_rows
                    ],
                    "attempts": attempts_payload,
                }
            )
        run_details = details
    return RequestStatus(
        request_id=request_id,
        status=req_state.get("status", row["status"]),
        chosen_run_id=req_state.get("chosen_run_id"),
        winner_run_id=req_state.get("winner_run_id", req_state.get("chosen_run_id")),
        runs=req_state.get("runs"),
        chosen_pod_id=req_state.get("chosen_pod_id"),
        dna_id=(result or {}).get("dna_id"),
        # Legacy aliases preserved for compatibility; winner_* fields are canonical.
        executor_output_artifact_path=artifacts.get("winner_executor_output", artifacts.get("executor_output")),
        judge_result_artifact_path=artifacts.get("winner_judge_result", artifacts.get("judge_result")),
        winner_executor_output_artifact_path=artifacts.get("winner_executor_output", artifacts.get("executor_output")),
        winner_judge_result_artifact_path=artifacts.get("winner_judge_result", artifacts.get("judge_result")),
        now_slice_artifact_path=artifacts.get("now_slice"),
        result=result,
        run_details=run_details,
    )


@router.get("/pods")
def list_pods(state: AppState = Depends(get_state)) -> dict:
    rows = state.db.fetchall("SELECT pod_id, is_enabled, config_json FROM pods ORDER BY pod_id")
    return {
        "pods": [
            {
                "pod_id": r["pod_id"],
                "enabled": bool(r["is_enabled"]),
                "config": r["config_json"],
                "weight": state.router.weights.get(r["pod_id"], 1.0),
                "typed_weights": state.router.weights_by_type,
            }
            for r in rows
        ]
    }


@router.get("/attractors")
def list_attractors(
    window: int = 200,
    group_by: str = "global",
    sort: str = "frequency",
    limit: int = 10,
    min_count: int = 1,
    state: AppState = Depends(get_state),
) -> dict:
    if window < 1 or window > 5000:
        raise HTTPException(status_code=400, detail="window must be between 1 and 5000")
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    if min_count < 1 or min_count > 1000:
        raise HTTPException(status_code=400, detail="min_count must be between 1 and 1000")
    if group_by not in {"global", "pod"}:
        raise HTTPException(status_code=400, detail="group_by must be one of: global, pod")
    if sort not in {"frequency", "retry_rate", "repair_rate", "fail_rate"}:
        raise HTTPException(status_code=400, detail="sort must be one of: frequency, retry_rate, repair_rate, fail_rate")

    rows = state.db.attractor_rows(window=window, group_by=group_by)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        scope = "global" if group_by == "global" else str(row["pod_id"])
        run_id = str(row["run_id"])
        winner_attempt_id = str(row["winner_attempt_id"] or "")
        if not winner_attempt_id:
            wa = state.db.get_winner_attempt(run_id)
            if wa is not None:
                winner_attempt_id = str(wa["attempt_id"])
        if not winner_attempt_id:
            winner_attempt_id = f"att_legacy_{run_id[-12:]}"
        winner_attempt_num = int(row["winner_attempt_num"])
        retried = int(row["attempt_count"]) > 1
        winner_pass = int(row["pass"]) == 1
        repaired = retried and winner_pass and winner_attempt_num > 1
        groups[scope].append(
            {
                "run_id": run_id,
                "pod_id": str(row["pod_id"]),
                "dna_id": str(row["dna_id"]),
                "behavior_fp": str(row["behavior_fp"]),
                "winner_attempt_num": winner_attempt_num,
                "winner_attempt_id": winner_attempt_id,
                "pass": int(winner_pass),
                "retried": retried,
                "repaired": repaired,
                "latency_ms": int(row["latency_ms"] or 0),
                "attempt_count": int(row["attempt_count"] or 1),
                "uses_workspace": int(row["workspace_ops_count"] or 0) > 0,
                "commit_attempted": int(row["knowledge_commit_attempts"] or 0) > 0,
                "commit_passed": int(row["knowledge_commits_count"] or 0) > 0,
            }
        )

    result_items: list[dict] = []
    for scope, scope_rows in groups.items():
        by_fp: dict[str, list[dict]] = defaultdict(list)
        for row in scope_rows:
            by_fp[row["behavior_fp"]].append(row)

        ranked = sorted(by_fp.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for behavior_fp, fp_rows in ranked:
            pod_counts: dict[str, int] = defaultdict(int)
            dna_counts: dict[str, int] = defaultdict(int)
            sample = []
            features = None
            for r in fp_rows:
                pod_counts[r["pod_id"]] += 1
                dna_counts[r["dna_id"]] += 1
                if len(sample) < 3:
                    sample.append(
                        {
                            "run_id": r["run_id"],
                            "winner_attempt_num": r["winner_attempt_num"],
                            "winner_attempt_id": r["winner_attempt_id"],
                            "pod_id": r["pod_id"],
                            "dna_id": r["dna_id"],
                        }
                    )
                if features is None and r["winner_attempt_id"]:
                    winner_attempt = state.db.get_winner_attempt(str(r["run_id"]))
                    if winner_attempt is not None and winner_attempt["artifact_executor_path"]:
                        try:
                            executor_output = _read_json_from_data_path(state, str(winner_attempt["artifact_executor_path"]))
                            features = behavior_features(executor_output)
                        except Exception:
                            features = None
            count_total = len(fp_rows)
            if count_total < min_count:
                continue
            count_repaired = sum(1 for r in fp_rows if r["repaired"])
            count_retried = sum(1 for r in fp_rows if r["retried"])
            count_success = sum(int(r["pass"]) for r in fp_rows)
            count_failed = count_total - count_success
            latencies = sorted(max(0, int(r["latency_ms"])) for r in fp_rows)
            p95_index = max(0, ceil(0.95 * len(latencies)) - 1)
            p95_latency_ms = int(latencies[p95_index]) if latencies else 0
            attempt_counts = sorted(max(1, int(r["attempt_count"])) for r in fp_rows)
            avg_attempt_count = round(sum(max(1, int(r["attempt_count"])) for r in fp_rows) / max(1, count_total), 4)
            result_items.append(
                {
                    "scope": scope,
                    "behavior_fp": behavior_fp,
                    "count_total": count_total,
                    "count_success": count_success,
                    "count_failed": count_failed,
                    "count_retried": count_retried,
                    "count_repaired": count_repaired,
                    "retry_rate": round(count_retried / max(1, count_total), 4),
                    "repair_rate": round(count_repaired / max(1, count_total), 4),
                    "repair_success_rate": round(count_repaired / max(1, count_retried), 4) if count_retried > 0 else 0.0,
                    "median_latency_ms": int(median(latencies)) if latencies else 0,
                    "p95_latency_ms": p95_latency_ms,
                    "avg_attempt_count": avg_attempt_count,
                    "median_attempt_count": int(median(attempt_counts)) if attempt_counts else 1,
                    "features": features,
                    "workspace_signature": {
                        "uses_workspace": any(bool(r["uses_workspace"]) for r in fp_rows),
                        "commit_attempted": any(bool(r["commit_attempted"]) for r in fp_rows),
                        "commit_passed": any(bool(r["commit_passed"]) for r in fp_rows),
                    },
                    "top_pods": [
                        {"pod_id": pod_id, "count": count}
                        for pod_id, count in sorted(pod_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
                    ],
                    "top_dna": [
                        {"dna_id": dna_id, "count": count}
                        for dna_id, count in sorted(dna_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
                    ],
                    "sample": sample,
                }
            )

    if sort == "retry_rate":
        result_items.sort(key=lambda item: (-float(item["retry_rate"]), -int(item["count_total"]), item["behavior_fp"], item["scope"]))
    elif sort == "repair_rate":
        result_items.sort(key=lambda item: (-float(item["repair_rate"]), -int(item["count_total"]), item["behavior_fp"], item["scope"]))
    elif sort == "fail_rate":
        result_items.sort(
            key=lambda item: (
                -round(int(item["count_failed"]) / max(1, int(item["count_total"])), 4),
                -int(item["count_total"]),
                item["behavior_fp"],
                item["scope"],
            )
        )
    else:
        result_items.sort(key=lambda item: (-int(item["count_total"]), item["behavior_fp"], item["scope"]))
    result_items = result_items[:limit]
    return {"window": window, "group_by": group_by, "sort": sort, "min_count": min_count, "items": result_items}


@router.get("/leaderboard/{request_type}")
def leaderboard(request_type: str, state: AppState = Depends(get_state)) -> dict:
    leaders = state.db.leaderboard_for_request_type(request_type=request_type, limit=10)
    return {"request_type": request_type, "leaders": leaders}


@router.get("/resources/{request_type}")
def resources(request_type: str, state: AppState = Depends(get_state)) -> dict:
    rows = state.db.list_pod_resource_state(request_type=request_type)
    return {
        "request_type": request_type,
        "resources": [
            {
                "pod_id": str(r["pod_id"]),
                "compute_budget": float(r["compute_budget"]),
                "traffic_cap": float(r["traffic_cap"]),
                "incubation_budget": int(r["incubation_budget"]),
                "is_starved": bool(r["is_starved"]),
                "assigned_requests": int(r["assigned_requests"]),
            }
            for r in rows
        ],
    }


@router.post("/replay/{run_id}")
def replay(run_id: str, payload: ReplayRequest, state: AppState = Depends(get_state)) -> dict:
    row = state.db.fetchone(
        "SELECT request_id, pod_id, snapshot_id, executor_output_id, judge_result_id FROM runs WHERE run_id = ?",
        (run_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    pod_id = payload.pod_id or row["pod_id"]
    if pod_id not in state.pods:
        raise HTTPException(status_code=404, detail="pod not found")

    snapshot_path = state.db.fetch_snapshot_path(str(row["snapshot_id"]))
    if not snapshot_path:
        raise HTTPException(status_code=404, detail="snapshot artifact not found")
    snapshot_json = state.pods[pod_id].artifact_store.get_json(snapshot_path)
    replay_snapshot = Snapshot.model_validate(snapshot_json)
    pod = state.pods[pod_id]
    replay_snapshot = _snapshot_with_replay_overrides(
        snapshot=replay_snapshot,
        persona_id=(str(payload.persona_id).strip() if payload.persona_id else None),
        persona_version=(
            str(payload.persona_version).strip()
            if payload.persona_version
            else (_persona_version(state, str(payload.persona_id).strip()) if payload.persona_id else None)
        ),
    )
    original_dna = pod.dna
    original_dna_pool = dict(pod.dna_pool)
    override_dna = _hydrate_replay_dna(state=state, pod=pod, dna_id=str(payload.dna_id)) if payload.dna_id else None
    try:
        if override_dna is not None:
            pod.dna = override_dna
            pod.dna_pool = {override_dna.dna_id: override_dna}
        replay_result = pod.run(replay_snapshot)
    finally:
        pod.dna = original_dna
        pod.dna_pool = original_dna_pool
    replay_result = _normalize_request_result(state, replay_result)
    replay_edge = make_lineage_edge(
        parent_type="artifact",
        parent_id=f"run:{run_id}",
        child_type="artifact",
        child_id=f"run:{replay_result['run_id']}",
        reason="replay",
        run_id=replay_result["run_id"],
    )
    state.db.insert_lineage_edge(
        edge_id=replay_edge.edge_id,
        parent_type=replay_edge.parent_type,
        parent_id=replay_edge.parent_id,
        child_type=replay_edge.child_type,
        child_id=replay_edge.child_id,
        reason=replay_edge.reason,
        run_id=replay_edge.run_id,
        created_at=replay_edge.created_at,
        metadata_json=json.dumps({"source_run_id": run_id, "replay_run_id": replay_result["run_id"]}),
    )

    src_exec_row = state.db.fetchone(
        "SELECT artifact_path FROM executor_outputs WHERE executor_output_id = ?",
        (row["executor_output_id"],),
    )
    src_judge_row = state.db.fetchone(
        "SELECT artifact_path FROM judge_results WHERE judge_result_id = ?",
        (row["judge_result_id"],),
    )
    regression = {"executor_hash_match": None, "judge_hash_match": None}
    if src_exec_row:
        src_exec = state.pods[pod_id].artifact_store.get_json(str(src_exec_row["artifact_path"]))
        regression["executor_hash_match"] = canonical_sha256(src_exec) == canonical_sha256(replay_result["executor_output"])
    if src_judge_row:
        src_judge = state.pods[pod_id].artifact_store.get_json(str(src_judge_row["artifact_path"]))
        regression["judge_hash_match"] = canonical_sha256(src_judge) == canonical_sha256(replay_result["judge_result"])

    return {
        "source_run_id": run_id,
        "replay_run_id": replay_result["run_id"],
        "source_snapshot_id": str(row["snapshot_id"]),
        "replay_snapshot_id": replay_result["snapshot_id"],
        "applied_overrides": {
            "dna_id": replay_result["dna_id"] if payload.dna_id else None,
            "persona_id": str(payload.persona_id or "") or None,
            "persona_version": str(
                payload.persona_version
                or (_persona_version(state, str(payload.persona_id).strip()) if payload.persona_id else "")
            )
            or None,
        },
        "result": replay_result,
        "regression": regression,
    }


@router.post("/signals")
def ingest_signal(payload: SignalIngestRequest, state: AppState = Depends(get_state)) -> dict:
    allowed = {"retry", "abandon", "return", "latency", "completion", "time_to_resolution_ms"}
    if payload.signal_type not in allowed:
        raise HTTPException(status_code=400, detail=f"signal_type must be one of {sorted(allowed)}")
    if payload.request_id is None and payload.pod_id is None:
        raise HTTPException(status_code=400, detail="request_id or pod_id is required")

    pod_id = payload.pod_id
    request_type = None
    if pod_id is None and payload.request_id:
        req_state = state.requests.get(payload.request_id, {})
        pod_id = req_state.get("chosen_pod_id")
        result = req_state.get("result") or {}
        request_type = result.get("request_type")
        if pod_id is None:
            row = state.db.fetchone(
                "SELECT pod_id FROM runs WHERE request_id = ? ORDER BY created_at DESC LIMIT 1",
                (payload.request_id,),
            )
            if row:
                pod_id = str(row["pod_id"])
    if request_type is None and payload.request_id:
        req_row = state.db.fetchone("SELECT request_type FROM requests WHERE request_id = ?", (payload.request_id,))
        if req_row:
            request_type = str(req_row["request_type"])

    normalized_signal = "latency" if payload.signal_type == "time_to_resolution_ms" else payload.signal_type
    signal_id = _record_signal(
        state=state,
        request_id=payload.request_id,
        pod_id=pod_id,
        request_type=request_type,
        signal_type=normalized_signal,
        value=payload.value,
        metadata=payload.metadata,
    )
    weight = None
    if pod_id and request_type and pod_id in state.router.weights:
        weight = _update_routing_weight_from_signals(state, pod_id, request_type)
    return {"signal_id": signal_id, "pod_id": pod_id, "request_type": request_type, "updated_weight": weight}


@router.post("/actions/approvals")
def create_action_approval(payload: ActionApprovalCreateRequest, state: AppState = Depends(get_state)) -> dict:
    approval_id = f"appr_{uuid.uuid4().hex[:12]}"
    state.db.create_action_approval(
        approval_id=approval_id,
        created_at=_now_iso(),
        tool=payload.tool,
        request_id=payload.request_id,
        pod_id=payload.pod_id,
        expires_at=payload.expires_at,
        metadata_json=json.dumps(payload.metadata or {}),
    )
    return {"approval_id": approval_id, "status": "pending"}


@router.post("/actions/approvals/{approval_id}/approve")
def approve_action_approval(approval_id: str, state: AppState = Depends(get_state)) -> dict:
    row = state.db.fetchone("SELECT approval_id FROM action_approvals WHERE approval_id = ?", (approval_id,))
    if not row:
        raise HTTPException(status_code=404, detail="approval not found")
    state.db.approve_action(approval_id=approval_id, approved_at=_now_iso())
    return {"approval_id": approval_id, "status": "approved"}


@router.post("/pod-generator/generate")
def generate_pods(payload: PodGenerateRequest, state: AppState = Depends(get_state)) -> dict:
    if payload.count < 1 or payload.count > 4:
        raise HTTPException(status_code=400, detail="count must be between 1 and 4")
    generator = PodGenerator(db=state.db, artifact_root=state.data_dir / "artifacts")
    variants = generator.generate(count=payload.count, request_type=payload.request_type)

    created = []
    for v in variants:
        _materialize_generated_pod(variant=v, state=state)
        created.append({"pod_id": v.pod_id, "parent_pod_id": v.parent_pod_id, "config": v.config})
    return {"created": created}


@router.post("/router")
def route_persona(payload: RouterRequest, state: AppState = Depends(get_state)) -> dict:
    router_artifact = _build_router_artifact(payload)
    artifact_id, artifact_path = _persist_router_artifact(router_artifact=router_artifact, state=state)
    return {
        "artifact_id": artifact_id,
        "artifact_path": _portable_ref_path(state, artifact_path),
        "relative_path": _portable_ref_path(state, artifact_path),
        **router_artifact,
    }


def _execute_workflow_request(payload: WorkflowRequest, state: AppState) -> dict:
    max_steps = max(1, min(int(payload.max_steps), 12))
    spawn_count = max(1, min(int(payload.spawn_count), 2))
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    request_id = str(payload.request_id or state.new_request_id())
    created_at = _now_iso()
    answers_text = _answers_text(payload.clarification_answers or {})
    effective_user_input = payload.user_input if not answers_text else f"{payload.user_input}\n\n{answers_text}"
    workflow_lock_acquired = False
    has_lock_api = hasattr(state, "try_begin_workflow") and hasattr(state, "end_workflow")
    if has_lock_api and not payload.handoff_artifact_id:
        workflow_lock_acquired = bool(
            state.try_begin_workflow(  # type: ignore[attr-defined]
                workflow_id=workflow_id,
                request_id=request_id,
                user_input=effective_user_input,
            )
        )
        if not workflow_lock_acquired:
            active = state.get_active_workflow() if hasattr(state, "get_active_workflow") else None
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "workflow_already_running",
                    "active_workflow": active,
                },
            )
    request_type = classify_request_type(effective_user_input, payload.request_type)
    existing_request = state.db.fetchone("SELECT request_id FROM requests WHERE request_id = ?", (request_id,))
    if existing_request:
        state.db.update_request_status(request_id=request_id, status="running")
    else:
        state.db.insert_request(
            request_id=request_id,
            created_at=created_at,
            status="running",
            user_input=effective_user_input,
            request_type=request_type,
            constraints_json=None,
        )
    state.requests[request_id] = {
        "status": "running",
        "workflow_id": workflow_id,
        "user_input": payload.user_input,
        "request_type": request_type,
    }

    enabled = state.db.fetchall("SELECT pod_id FROM pods WHERE is_enabled = 1 ORDER BY pod_id")
    candidate_pods = [str(r["pod_id"]) for r in enabled if str(r["pod_id"]) in state.pods]
    forced_pod_id = str(payload.forced_pod_id).strip() if payload.forced_pod_id else ""
    if forced_pod_id:
        pod_id = forced_pod_id
    else:
        decision = state.router.route(request_type, mode="weighted", pod_ids=candidate_pods)
        pod_id = decision.pod_ids[0] if decision.pod_ids else (candidate_pods[0] if candidate_pods else "")
    if not pod_id or pod_id not in state.pods:
        if workflow_lock_acquired and has_lock_api:
            state.end_workflow(workflow_id=workflow_id)  # type: ignore[attr-defined]
        raise HTTPException(status_code=503, detail="no enabled pods available")
    pod = state.pods[pod_id]

    base_snapshot = build_snapshot(
        request_id=request_id,
        user_input=effective_user_input,
        request_type=request_type,
        policies=SnapshotPolicies(
            tool_policy_id="tp_default",
            allowed_tools=["http_get", "fs_read"],
            forbidden_tools=["shell_exec", "fs_write"],
            budgets={"max_total_tool_calls": 5, "max_http_get": 3},
        ),
        context_state={},
    )

    workflow_steps: list[dict] = []
    workflow_edges: list[dict] = []
    pending_switch: dict | None = None
    last_now_slice: dict | None = None
    selected_persona_id = "general"
    planner_artifact = _build_planner_artifact(user_input=effective_user_input, requested_target=payload.canonical_target)
    planner_artifact_id, planner_artifact_path = _persist_planner_artifact(planner_artifact=planner_artifact, state=state)
    decomposition_plan = _build_decomposition_plan(planner_artifact=planner_artifact)
    decomposition_plan_artifact_id, decomposition_plan_artifact_path = _persist_decomposition_plan(
        decomposition_plan=decomposition_plan,
        state=state,
    )
    canonical_target = str(planner_artifact.get("canonical_target") or "").strip().lower()
    canonical_persona_plan = [str(x) for x in (planner_artifact.get("persona_plan") or [])]
    workspace_id: Optional[str] = None
    try:
        # Create workspace up-front so every NOW slice has a concrete writable container.
        ws = state.workspace.create_workspace(run_id=request_id)
        workspace_id = str(ws["workspace_id"])
    except Exception:
        workspace_id = None
    clarification_questions = _build_clarification_questions(planner_artifact=planner_artifact)
    planner_confidence = float(planner_artifact.get("confidence") or 0.0)
    needs_clarification = bool(clarification_questions) and not payload.clarification_answers and (
        not canonical_target or planner_confidence < 0.75
    )
    if needs_clarification:
        clarification_workspace_manifest = None
        if workspace_id:
            try:
                clarification_workspace_manifest = state.workspace.get_workspace(workspace_id=workspace_id)
            except Exception:
                clarification_workspace_manifest = None
        clarification_payload = {
            "request_id": request_id,
            "workflow_id": workflow_id,
            "canonical_target": canonical_target or None,
            "questions": clarification_questions,
            "planner_artifact_id": planner_artifact_id,
            "planner_artifact_path": _portable_ref_path(state, planner_artifact_path),
            "planner_relative_path": _portable_ref_path(state, planner_artifact_path),
            "decomposition_plan_artifact_id": decomposition_plan_artifact_id,
            "decomposition_plan_artifact_path": _portable_ref_path(state, decomposition_plan_artifact_path),
            "decomposition_plan_relative_path": _portable_ref_path(state, decomposition_plan_artifact_path),
        }
        state.requests[request_id] = {
            "status": "needs_clarification",
            "workflow_id": workflow_id,
            "payload": payload.model_dump(),
            "clarification": clarification_payload,
        }
        state.db.update_request_status(request_id=request_id, status="pending")
        if workflow_lock_acquired and has_lock_api:
            state.end_workflow(workflow_id=workflow_id)  # type: ignore[attr-defined]
        return {
            "workflow_id": workflow_id,
            "request_id": request_id,
            "pod_id": pod_id,
            "steps": [],
            "workflow_graph_artifact_id": None,
            "workflow_graph_artifact_path": None,
            "workflow_graph_relative_path": None,
            "planner_artifact_id": planner_artifact_id,
            "planner_artifact_path": _portable_ref_path(state, planner_artifact_path),
            "planner_relative_path": _portable_ref_path(state, planner_artifact_path),
            "planner": planner_artifact,
            "decomposition_plan_artifact_id": decomposition_plan_artifact_id,
            "decomposition_plan_artifact_path": _portable_ref_path(state, decomposition_plan_artifact_path),
            "decomposition_plan_relative_path": _portable_ref_path(state, decomposition_plan_artifact_path),
            "decomposition_plan": decomposition_plan,
            "canonical_target": canonical_target or None,
            "workspace_id": workspace_id,
            "workspace_manifest": clarification_workspace_manifest,
            "service": None,
            "service_url": None,
            "service_hello_url": None,
            "auto_commit": {"attempted": False, "reason": "needs_clarification"},
            "learning": {"attempted": False, "reason": "needs_clarification"},
            "final_winner_run_id": None,
            "final_status": "needs_clarification",
            "final_pass": False,
            "stop_reason": "needs_clarification",
            "spawn": {"attempted": False, "reason": "needs_clarification"},
            "clarification": clarification_payload,
        }
    router_decision_payload = RouterRequest(
        user_input=effective_user_input,
        current_now_slice=None,
        workspace_refs=payload.workspace_refs,
        allowed_personas=payload.allowed_personas,
    )
    router_artifact = _build_router_artifact(router_decision_payload)
    router_artifact_id, _ = _persist_router_artifact(router_artifact=router_artifact, state=state)
    selected_persona_id = str(router_artifact["selected_persona_id"])
    handoff_artifact_id: Optional[str] = payload.handoff_artifact_id or router_artifact_id
    retry_same_persona_used = False

    final_result = None
    final_failure_summary = ""
    step_index = 0
    bug_repair_hops = 0
    implementation_non_actionable_hops = 0
    workflow_stop_reason: str | None = None
    repair_instruction: str | None = None
    while step_index < max_steps:
        if canonical_persona_plan:
            if step_index >= len(canonical_persona_plan):
                break
            selected_persona_id = canonical_persona_plan[step_index]
        persona_version = _persona_version(state, selected_persona_id)
        state_update = dict(base_snapshot.context.state or {})
        state_update["persona_id"] = selected_persona_id
        state_update["persona_version"] = persona_version
        required_tools: list[str] = []
        if canonical_target:
            state_update["workflow_goal"] = canonical_target
            required_tools_by_target: dict[str, dict[str, list[str]]] = {
                "ui_page_update": {
                    "research": ["search_local_kb"],
                    "implementation": ["write_file"],
                    "qa_test": ["workspace_run"],
                },
                "service_bootstrap_app": {
                    "research": ["search_local_kb"],
                    "implementation": ["write_file"],
                    "qa_test": ["workspace_run"],
                },
                "hello_fastapi_service": {
                    "research": ["search_local_kb"],
                    "implementation": ["write_file"],
                    "qa_test": ["workspace_run"],
                },
                "weather_station_app": {
                    "research": ["search_local_kb"],
                    "implementation": ["write_file"],
                    "qa_test": ["workspace_run"],
                },
                "generic_build_app": {
                    "research": ["search_local_kb"],
                    "implementation": ["write_file"],
                    "qa_test": ["workspace_run"],
                },
            }
            required_tools = required_tools_by_target.get(canonical_target, {}).get(selected_persona_id, [])
            state_update["workflow_projection"] = _build_persona_thread_projection(
                planner_artifact=planner_artifact,
                decomposition_plan=decomposition_plan,
                planner_artifact_id=str(planner_artifact_id),
                decomposition_plan_artifact_id=str(decomposition_plan_artifact_id),
                canonical_target=canonical_target,
                persona_id=selected_persona_id,
                step_index=step_index,
                required_tools=required_tools,
                handoff_artifact_id=handoff_artifact_id,
                workspace_refs=payload.workspace_refs,
                workspace_id=workspace_id,
                request_id=request_id,
            )
            if required_tools:
                state_update["required_tools"] = required_tools
                state_update["tool_call_contract_hint"] = (
                    "Return JSON with response + tool_calls. "
                    f"tool_calls must include: {', '.join(required_tools)}."
                )
                arg_contracts = {
                    "search_local_kb": "args must include query (string) and optional max_hits (int).",
                    "write_file": "args must include path (string) and content (string).",
                    "workspace_run": "args must include cmd (string) and timeout_s (int). Use pytest command only.",
                }
                state_update["tool_arg_contract_hint"] = " ".join(
                    arg_contracts[t] for t in required_tools if t in arg_contracts
                )
        if workspace_id:
            state_update["workspace_id"] = workspace_id
            state_update["world_container"] = {
                "workspace_id": workspace_id,
                "root": f"data/workspaces/{workspace_id}",
                "deploy_target": {
                    "type": "workspace_hello_service",
                    "health_path": "/health",
                },
                "ui_page": {
                    "url": "http://127.0.0.1:8000",
                    "source_files": ["apps/ui/src/main.jsx", "apps/ui/styles.css", "apps/ui/index.html"],
                },
            }
        else:
            state_update["world_container"] = {
                "workspace_id": None,
                "root": "data/workspaces",
                "deploy_target": {
                    "type": "workspace_hello_service",
                    "health_path": "/health",
                },
                "ui_page": {
                    "url": "http://127.0.0.1:8000",
                    "source_files": ["apps/ui/src/main.jsx", "apps/ui/styles.css", "apps/ui/index.html"],
                },
            }
            wp = state_update.get("workflow_projection")
            if isinstance(wp, dict):
                traceability = wp.get("traceability")
                if isinstance(traceability, dict):
                    traceability["workspace_id"] = workspace_id
        if handoff_artifact_id:
            state_update["handoff_artifact_id"] = handoff_artifact_id
        if repair_instruction:
            state_update["repair_instruction"] = repair_instruction
        step_snapshot = base_snapshot.model_copy(update={"context": base_snapshot.context.model_copy(update={"state": state_update})})
        try:
            run_result = pod.run(step_snapshot)
        except Exception as exc:
            run_result = pod.record_failed_run(step_snapshot, exc)
        final_result = run_result
        winner_attempt_num = int(run_result.get("winner_attempt_num", 1))
        attempts = run_result.get("attempts", []) or []
        winner_attempt_id = str(attempts[max(0, winner_attempt_num - 1)]) if attempts else None
        workflow_steps.append(
            {
                "run_id": run_result["run_id"],
                "attempt_id": winner_attempt_id,
                "persona_id": selected_persona_id,
                "persona_version": persona_version,
                "handoff_artifact_id": handoff_artifact_id,
                "status": run_result["status"],
                "pass": bool((run_result.get("judge_result") or {}).get("pass", False)),
            }
        )
        if workspace_id is None:
            try:
                ws = state.workspace.create_workspace(run_id=str(run_result["run_id"]))
                workspace_id = str(ws["workspace_id"])
            except Exception:
                workspace_id = None
        if len(workflow_steps) > 1:
            prev = workflow_steps[-2]
            workflow_edges.append(
                {
                    "from_run_id": prev.get("run_id"),
                    "from_attempt_id": prev.get("attempt_id"),
                    "to_run_id": run_result["run_id"],
                    "to_attempt_id": winner_attempt_id,
                    "reason": (
                        "persona_switch"
                        if str(prev.get("persona_id")) != str(selected_persona_id)
                        else "retry_same_persona"
                    ),
                    "handoff_artifact_id": handoff_artifact_id,
                }
            )
        if pending_switch is not None and winner_attempt_id:
            edge = make_lineage_edge(
                parent_type="run_attempt",
                parent_id=str(pending_switch["from_attempt_id"]),
                child_type="run_attempt",
                child_id=winner_attempt_id,
                reason="persona_switch",
                run_id=run_result["run_id"],
            )
            state.db.insert_lineage_edge(
                edge_id=edge.edge_id,
                parent_type=edge.parent_type,
                parent_id=edge.parent_id,
                child_type=edge.child_type,
                child_id=edge.child_id,
                reason=edge.reason,
                run_id=edge.run_id,
                created_at=edge.created_at,
                metadata_json=json.dumps(
                    {
                        "from_persona_id": pending_switch["from_persona_id"],
                        "to_persona_id": selected_persona_id,
                        "handoff_artifact_id": handoff_artifact_id,
                        "workflow_id": workflow_id,
                    }
                ),
            )
            pending_switch = None

        judge_pass = bool((run_result.get("judge_result") or {}).get("pass", False))
        if judge_pass and not canonical_persona_plan:
            break
        if judge_pass and canonical_persona_plan:
            retry_same_persona_used = False
            if selected_persona_id in {"qa_test", "code_review"}:
                repair_instruction = None
            step_index += 1
            continue

        now_slice_path = (run_result.get("artifacts") or {}).get("now_slice")
        if now_slice_path:
            try:
                last_now_slice = _read_json_from_data_path(state, str(now_slice_path))
            except Exception:
                last_now_slice = None
        failures = (run_result.get("judge_result") or {}).get("failures", []) or []
        failure_summary = "; ".join(str(f.get("detail", "")) for f in failures if isinstance(f, dict)).strip() or "judge_failed"
        final_failure_summary = failure_summary

        if payload.retry_same_persona_once and not retry_same_persona_used:
            retry_same_persona_used = True
            handoff_payload = {
                "type": "retry_same_persona",
                "failure_summary": failure_summary,
                "persona_id": selected_persona_id,
                "workflow_id": workflow_id,
                "from_run_id": run_result["run_id"],
            }
            handoff_artifact_id, handoff_path = state.router_artifact_store.put_json(handoff_payload)
            state.db.insert_artifact_registry(
                artifact_id=handoff_artifact_id,
                created_at=_now_iso(),
                artifact_type="handoff_artifact",
                content_hash=canonical_sha256(handoff_payload),
                artifact_path=handoff_path,
                metadata_json=json.dumps({"persona_id": selected_persona_id, "workflow_id": workflow_id}),
            )
            continue

        retry_same_persona_used = False
        if canonical_persona_plan:
            if selected_persona_id == "implementation" and _is_non_actionable_implementation_failure(failures):
                implementation_non_actionable_hops += 1
                if implementation_non_actionable_hops <= 1:
                    repair_instruction = _implementation_repair_instruction_for_target(canonical_target)
                    handoff_payload = {
                        "type": "implementation_fast_fail_feedback",
                        "failure_summary": failure_summary,
                        "from_persona_id": "implementation",
                        "to_persona_id": "implementation",
                        "workflow_id": workflow_id,
                        "from_run_id": run_result["run_id"],
                        "repair_instruction": repair_instruction,
                    }
                    handoff_artifact_id, handoff_path = state.router_artifact_store.put_json(handoff_payload)
                    state.db.insert_artifact_registry(
                        artifact_id=handoff_artifact_id,
                        created_at=_now_iso(),
                        artifact_type="handoff_artifact",
                        content_hash=canonical_sha256(handoff_payload),
                        artifact_path=handoff_path,
                        metadata_json=json.dumps({"persona_id": "implementation", "workflow_id": workflow_id}),
                    )
                    continue
                workflow_stop_reason = "implementation_fast_fail_non_actionable"
                break
            if selected_persona_id in {"qa_test", "code_review"} and _is_actionable_bug_failure(failures):
                bug_repair_hops += 1
                if bug_repair_hops <= 2:
                    repair_instruction = (
                        f"{selected_persona_id} reported actionable failure: "
                        + failure_summary
                        + ". "
                        + _implementation_repair_instruction_for_target(canonical_target)
                        + " Ensure tests validate endpoint behavior via FastAPI TestClient; avoid source-text assertions."
                    )
                    handoff_payload = {
                        "type": "bug_repair_feedback",
                        "failure_summary": failure_summary,
                        "from_persona_id": selected_persona_id,
                        "to_persona_id": "implementation",
                        "workflow_id": workflow_id,
                        "from_run_id": run_result["run_id"],
                        "repair_instruction": repair_instruction,
                        "bug_repair_hop": bug_repair_hops,
                    }
                    handoff_artifact_id, handoff_path = state.router_artifact_store.put_json(handoff_payload)
                    state.db.insert_artifact_registry(
                        artifact_id=handoff_artifact_id,
                        created_at=_now_iso(),
                        artifact_type="handoff_artifact",
                        content_hash=canonical_sha256(handoff_payload),
                        artifact_path=handoff_path,
                        metadata_json=json.dumps({"persona_id": "implementation", "workflow_id": workflow_id}),
                    )
                    if "implementation" in canonical_persona_plan:
                        step_index = canonical_persona_plan.index("implementation")
                    else:
                        step_index = max(0, step_index - 1)
                    continue
                workflow_stop_reason = "bug_repair_hop_limit_reached"
                break
            step_index += 1
            continue
        next_router_input = RouterRequest(
            user_input=f"{payload.user_input}\nFailure summary: {failure_summary}",
            current_now_slice=last_now_slice,
            workspace_refs=payload.workspace_refs,
            allowed_personas=payload.allowed_personas,
        )
        next_router = _build_router_artifact(next_router_input)
        next_handoff_artifact_id, _ = _persist_router_artifact(router_artifact=next_router, state=state)
        pending_switch = {
            "from_attempt_id": winner_attempt_id,
            "from_persona_id": selected_persona_id,
        }
        selected_persona_id = str(next_router["selected_persona_id"])
        handoff_artifact_id = next_handoff_artifact_id
        step_index += 1
        continue

    state.db.update_request_status(request_id=request_id, status="done")
    final_winner_run_id = str(final_result["run_id"]) if final_result else None
    workspace_manifest = None
    service = None
    if workspace_id:
        try:
            workspace_manifest = state.workspace.get_workspace(workspace_id=workspace_id)
        except Exception:
            workspace_manifest = None
        if canonical_target in {"hello_fastapi_service", "weather_station_app", "service_bootstrap_app"}:
            try:
                health_path = "/weather" if canonical_target == "weather_station_app" else "/health" if canonical_target == "service_bootstrap_app" else "/hello"
                service = state.workspace.start_hello_service(workspace_id=workspace_id, health_path=health_path)
            except Exception as exc:
                status = state.workspace.hello_service_status(workspace_id=workspace_id)
                service = {
                    "workspace_id": workspace_id,
                    "running": bool(status.get("running", False)),
                    "error": str(exc),
                    **status,
                }
    workflow_graph = {
        "workflow_id": workflow_id,
        "request_id": request_id,
        "created_at": created_at,
        "nodes": [
            {
                "node_id": f"node_{idx+1}",
                "run_id": step["run_id"],
                "attempt_id": step["attempt_id"],
                "persona_id": step["persona_id"],
                "persona_version": step["persona_version"],
                "status": step["status"],
                "pass": bool(step["pass"]),
            }
            for idx, step in enumerate(workflow_steps)
        ],
        "edges": workflow_edges,
        "final_winner_run_id": final_winner_run_id,
    }
    workflow_graph_artifact_id, workflow_graph_artifact_path = state.router_artifact_store.put_json(workflow_graph)
    state.db.insert_artifact_registry(
        artifact_id=workflow_graph_artifact_id,
        created_at=_now_iso(),
        artifact_type="workflow_graph",
        content_hash=canonical_sha256(workflow_graph),
        artifact_path=workflow_graph_artifact_path,
        metadata_json=json.dumps({"workflow_id": workflow_id, "request_id": request_id}),
    )
    auto_commit_result: dict[str, Any] | None = None
    if (
        payload.auto_commit
        and canonical_target in {"hello_fastapi_service", "weather_station_app", "service_bootstrap_app"}
        and workspace_id
        and final_result
        and bool((final_result.get("judge_result") or {}).get("pass", False))
    ):
        try:
            auto_commit_result = _run_auto_commit(
                state=state,
                run_id=str(final_result["run_id"]),
                workflow_id=workflow_id,
                canonical_target=str(canonical_target),
                workspace_id=str(workspace_id),
                planner_artifact_id=str(planner_artifact_id),
                workflow_graph_artifact_id=str(workflow_graph_artifact_id),
                run_ids=[str(step.get("run_id")) for step in workflow_steps if step.get("run_id")],
            )
        except Exception as exc:
            auto_commit_result = {"attempted": True, "pass": False, "failures": [str(exc)]}
    elif payload.auto_commit:
        auto_commit_result = {"attempted": False, "reason": "workflow_not_eligible"}
    else:
        auto_commit_result = {"attempted": False, "reason": "disabled_by_request"}
    learning_result: dict[str, Any] | None = None
    if payload.learn_mode:
        try:
            learning_result = _run_recursive_learning_curator(
                state=state,
                request_id=request_id,
                user_input=payload.user_input,
                canonical_target=(canonical_target or None),
                workflow_steps=workflow_steps,
                planner_artifact_id=str(planner_artifact_id),
                workflow_graph_artifact_id=str(workflow_graph_artifact_id),
                auto_commit_result=auto_commit_result,
            )
        except Exception as exc:
            learning_result = {"attempted": True, "pass": False, "error": str(exc)}
    spawn_result: dict[str, Any] = {"attempted": False, "reason": "disabled_by_request"}
    if payload.auto_spawn_on_failure and final_result and not bool((final_result.get("judge_result") or {}).get("pass", False)):
        tasks = decomposition_plan.get("tasks") or []
        if not tasks:
            spawn_result = {"attempted": False, "reason": "missing_decomposition_plan_tasks"}
        else:
            try:
                generator = PodGenerator(db=state.db, artifact_root=state.data_dir / "artifacts")
                variants = generator.generate(count=spawn_count, request_type=request_type)
                if not variants:
                    spawn_result = {"attempted": False, "reason": "generator_returned_no_variants"}
                else:
                    spawned = variants[0]
                    _materialize_generated_pod(variant=spawned, state=state)
                    spawn_handoff = {
                        "type": "pod_spawn_handoff",
                        "workflow_id": workflow_id,
                        "request_id": request_id,
                        "from_pod_id": pod_id,
                        "to_pod_id": spawned.pod_id,
                        "from_run_id": final_winner_run_id,
                        "failure_summary": final_failure_summary or "judge_failed",
                        "decomposition_plan_artifact_id": decomposition_plan_artifact_id,
                        "planner_artifact_id": planner_artifact_id,
                        "canonical_target": canonical_target or None,
                    }
                    spawn_handoff_artifact_id, spawn_handoff_path = state.router_artifact_store.put_json(spawn_handoff)
                    state.db.insert_artifact_registry(
                        artifact_id=spawn_handoff_artifact_id,
                        created_at=_now_iso(),
                        artifact_type="handoff_artifact",
                        content_hash=canonical_sha256(spawn_handoff),
                        artifact_path=spawn_handoff_path,
                        metadata_json=json.dumps({"workflow_id": workflow_id, "to_pod_id": spawned.pod_id}),
                    )
                    edge = make_lineage_edge(
                        parent_type="artifact",
                        parent_id=str(workflow_graph_artifact_id),
                        child_type="artifact",
                        child_id=str(spawn_handoff_artifact_id),
                        reason="pod_spawn_handoff",
                        run_id=final_winner_run_id,
                    )
                    state.db.insert_lineage_edge(
                        edge_id=edge.edge_id,
                        parent_type=edge.parent_type,
                        parent_id=edge.parent_id,
                        child_type=edge.child_type,
                        child_id=edge.child_id,
                        reason=edge.reason,
                        run_id=edge.run_id,
                        created_at=edge.created_at,
                        metadata_json=json.dumps({"workflow_id": workflow_id, "to_pod_id": spawned.pod_id}),
                    )
                    child_payload = payload.model_copy(
                        update={
                            "user_input": payload.user_input,
                            "canonical_target": canonical_target or payload.canonical_target,
                            "auto_spawn_on_failure": False,
                            "spawn_count": 1,
                            "forced_pod_id": spawned.pod_id,
                            "handoff_artifact_id": spawn_handoff_artifact_id,
                        }
                    )
                    child_workflow = run_workflow(payload=child_payload, state=state)
                    edge2 = make_lineage_edge(
                        parent_type="artifact",
                        parent_id=str(spawn_handoff_artifact_id),
                        child_type="artifact",
                        child_id=str(child_workflow.get("workflow_graph_artifact_id")),
                        reason="spawned_workflow",
                        run_id=str(child_workflow.get("final_winner_run_id")) if child_workflow.get("final_winner_run_id") else None,
                    )
                    state.db.insert_lineage_edge(
                        edge_id=edge2.edge_id,
                        parent_type=edge2.parent_type,
                        parent_id=edge2.parent_id,
                        child_type=edge2.child_type,
                        child_id=edge2.child_id,
                        reason=edge2.reason,
                        run_id=edge2.run_id,
                        created_at=edge2.created_at,
                        metadata_json=json.dumps({"workflow_id": workflow_id, "spawned_workflow_id": child_workflow.get("workflow_id")}),
                    )
                    spawn_result = {
                        "attempted": True,
                        "spawned_pod_id": spawned.pod_id,
                        "handoff_artifact_id": spawn_handoff_artifact_id,
                        "child_workflow_id": child_workflow.get("workflow_id"),
                        "child_request_id": child_workflow.get("request_id"),
                        "child_final_pass": bool(child_workflow.get("final_pass", False)),
                        "child_final_winner_run_id": child_workflow.get("final_winner_run_id"),
                    }
            except Exception as exc:
                spawn_result = {"attempted": True, "pass": False, "error": str(exc)}
    response_payload = {
        "workflow_id": workflow_id,
        "request_id": request_id,
        "pod_id": pod_id,
        "steps": workflow_steps,
        "workflow_graph_artifact_id": workflow_graph_artifact_id,
        "workflow_graph_artifact_path": _portable_ref_path(state, workflow_graph_artifact_path),
        "workflow_graph_relative_path": _portable_ref_path(state, workflow_graph_artifact_path),
        "planner_artifact_id": planner_artifact_id,
        "planner_artifact_path": _portable_ref_path(state, planner_artifact_path),
        "planner_relative_path": _portable_ref_path(state, planner_artifact_path),
        "planner": planner_artifact,
        "decomposition_plan_artifact_id": decomposition_plan_artifact_id,
        "decomposition_plan_artifact_path": _portable_ref_path(state, decomposition_plan_artifact_path),
        "decomposition_plan_relative_path": _portable_ref_path(state, decomposition_plan_artifact_path),
        "decomposition_plan": decomposition_plan,
        "canonical_target": canonical_target or None,
        "workspace_id": workspace_id,
        "workspace_manifest": workspace_manifest,
        "service": service,
        "service_url": (service.get("url") if isinstance(service, dict) else None),
        "service_hello_url": (service.get("hello_url") if isinstance(service, dict) else None),
        "auto_commit": auto_commit_result,
        "learning": learning_result,
        "final_winner_run_id": final_winner_run_id,
        "final_status": (str(final_result["status"]) if final_result else "failed"),
        "final_pass": bool((final_result or {}).get("judge_result", {}).get("pass", False)) if final_result else False,
        "stop_reason": workflow_stop_reason,
        "spawn": spawn_result,
    }
    state.requests[request_id] = {
        "status": "done",
        "workflow_id": workflow_id,
        "result": response_payload,
    }
    if workflow_lock_acquired and has_lock_api:
        state.end_workflow(workflow_id=workflow_id)  # type: ignore[attr-defined]
    return response_payload


@router.post("/workflows")
def run_workflow(payload: WorkflowRequest, state: AppState = Depends(get_state)) -> dict:
    return _execute_workflow_request(payload=payload, state=state)


@router.post("/workflows/{request_id}/resume")
def resume_workflow(request_id: str, payload: WorkflowResumeRequest, state: AppState = Depends(get_state)) -> dict:
    req_state = state.requests.get(request_id) or {}
    if str(req_state.get("status") or "") != "needs_clarification":
        raise HTTPException(status_code=409, detail="request is not waiting for clarification")
    original = req_state.get("payload")
    if not isinstance(original, dict):
        raise HTTPException(status_code=404, detail="missing stored workflow payload")
    original_payload = WorkflowRequest.model_validate(original)
    answers = {str(k): str(v) for k, v in (payload.answers or {}).items() if str(v).strip()}
    if not answers:
        raise HTTPException(status_code=400, detail="answers are required")
    resumed = original_payload.model_copy(
        update={
            "request_id": request_id,
            "clarification_answers": answers,
        }
    )
    return run_workflow(payload=resumed, state=state)


@router.get("/workflows/active")
def get_active_workflow(state: AppState = Depends(get_state)) -> dict:
    active = state.get_active_workflow() if hasattr(state, "get_active_workflow") else None
    return {"active": bool(active), "workflow": active}


@router.post("/workspace/lease")
def create_workspace_lease(payload: WorkspaceLeaseRequest, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.create_lease(
            run_id=payload.run_id,
            attempt_id=payload.attempt_id,
            capabilities=payload.capabilities,
            roots=payload.roots,
            budgets=payload.budgets,
            ttl_seconds=payload.ttl_seconds,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/workspace/write")
def workspace_write(payload: WorkspaceWriteRequest, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.write(lease_id=payload.lease_id, path=payload.path, content=payload.content)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/workspace/read")
def workspace_read(lease_id: str, path: str, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.read(lease_id=lease_id, path=path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/workspace/list")
def workspace_list(payload: WorkspaceListRequest, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.list(lease_id=payload.lease_id, path=payload.path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/knowledge/commit")
def knowledge_commit(payload: KnowledgeCommitRequest, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.commit_knowledge(
            lease_id=payload.lease_id,
            doc_key=payload.doc_key,
            title=payload.title,
            summary=payload.summary,
            extracted_facts=payload.extracted_facts,
            source_artifact_ids=payload.source_artifact_ids,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/knowledge/search")
def knowledge_search(q: str, limit: int = 20, lease_id: Optional[str] = None, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.search_knowledge(query=q, limit=limit, lease_id=lease_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/workspaces")
def create_workspace(payload: WorkspaceCreateRequest, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.create_workspace(run_id=payload.run_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.get_workspace(workspace_id=workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/workspaces/{workspace_id}/files")
def workspace_write_file(workspace_id: str, payload: WorkspaceFileWriteRequest, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.workspace_write_file(
            workspace_id=workspace_id,
            run_id=payload.run_id,
            path=payload.path,
            content=payload.content,
        )
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/workspaces/{workspace_id}/files/{file_path:path}")
def workspace_read_file(workspace_id: str, file_path: str, state: AppState = Depends(get_state)) -> dict:
    try:
        return state.workspace.workspace_read_file(workspace_id=workspace_id, path=file_path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("/commits/propose")
def propose_commit(payload: CommitProposeRequest, state: AppState = Depends(get_state)) -> dict:
    allowed_targets = {"dna_registry", "persona_registry", "playbooks", "tests"}
    if payload.target not in allowed_targets:
        raise HTTPException(status_code=400, detail=f"target must be one of {sorted(allowed_targets)}")
    proposal = {
        "proposal_id": f"pc_{uuid.uuid4().hex[:12]}",
        "run_id": payload.run_id,
        "target": payload.target,
        "changes": payload.changes,
        "summary": payload.summary,
        "created_at": _now_iso(),
    }
    artifact_id, artifact_path = state.router_artifact_store.put_json(proposal)
    state.db.insert_artifact_registry(
        artifact_id=artifact_id,
        created_at=_now_iso(),
        artifact_type="proposed_commit",
        content_hash=canonical_sha256(proposal),
        artifact_path=artifact_path,
        metadata_json=json.dumps({"target": payload.target, "run_id": payload.run_id}),
    )
    return {
        "artifact_id": artifact_id,
        "artifact_path": _portable_ref_path(state, artifact_path),
        "relative_path": _portable_ref_path(state, artifact_path),
        "proposal": proposal,
    }


@router.post("/commits/{proposal_artifact_id}/curate")
def curate_commit(
    proposal_artifact_id: str,
    payload: CommitCurateRequest,
    state: AppState = Depends(get_state),
) -> dict:
    row = state.db.fetchone(
        "SELECT artifact_type, artifact_path FROM artifacts WHERE artifact_id = ?",
        (proposal_artifact_id,),
    )
    if row is None or str(row["artifact_type"]) != "proposed_commit":
        raise HTTPException(status_code=404, detail="proposed_commit artifact not found")
    proposal = _read_json_from_data_path(state, str(row["artifact_path"]))
    proposal_text = json.dumps(proposal, sort_keys=True)
    failures: list[str] = []
    violation = detect_survival_awareness_violation(proposal_text)
    if violation is not None:
        failures.append("survival_awareness_leakage")
    lowered = proposal_text.lower()
    if any(tok in lowered for tok in ["evaluator gaming", "judge gaming", "hack the judge", "reward hack"]):
        failures.append("evaluator_gaming_risk")
    if str(proposal.get("run_id")) != payload.run_id:
        failures.append("run_id_mismatch")
    passed = len(failures) == 0
    if not passed:
        return {
            "proposal_artifact_id": proposal_artifact_id,
            "pass": False,
            "failures": failures,
        }
    commit_payload = {
        "commit_id": f"cmt_{uuid.uuid4().hex[:12]}",
        "proposal_artifact_id": proposal_artifact_id,
        "run_id": payload.run_id,
        "target": proposal["target"],
        "changes": proposal["changes"],
        "summary": proposal["summary"],
        "approved_by": payload.approver,
        "created_at": _now_iso(),
    }
    registry_apply = _apply_commit_to_registry(
        state=state,
        target=str(proposal["target"]),
        changes=proposal["changes"] if isinstance(proposal.get("changes"), dict) else {"raw": proposal.get("changes")},
        commit_id=str(commit_payload["commit_id"]),
    )
    commit_payload["registry"] = registry_apply
    commit_artifact_id, commit_artifact_path = state.router_artifact_store.put_json(commit_payload)
    state.db.insert_artifact_registry(
        artifact_id=commit_artifact_id,
        created_at=_now_iso(),
        artifact_type="commit",
        content_hash=canonical_sha256(commit_payload),
        artifact_path=commit_artifact_path,
        metadata_json=json.dumps({"target": proposal["target"], "run_id": payload.run_id}),
    )
    return {
        "proposal_artifact_id": proposal_artifact_id,
        "pass": True,
        "commit_artifact_id": commit_artifact_id,
        "commit_artifact_path": _portable_ref_path(state, commit_artifact_path),
        "commit_relative_path": _portable_ref_path(state, commit_artifact_path),
        "registry": registry_apply,
    }


@router.post("/research")
def research(payload: ResearchRequest, state: AppState = Depends(get_state)) -> dict:
    query = str(payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    hits = _search_local_kb(state=state, query=query, max_hits=max(1, min(int(payload.max_hits), 100)))
    report = {
        "research_report_id": f"rr_{uuid.uuid4().hex[:12]}",
        "run_id": payload.run_id,
        "query": query,
        "summary": f"Found {len(hits)} local evidence hits for query: {query}",
        "citations": hits,
        "created_at": _now_iso(),
    }
    artifact_id, artifact_path = state.router_artifact_store.put_json(report)
    state.db.insert_artifact_registry(
        artifact_id=artifact_id,
        created_at=_now_iso(),
        artifact_type="research_report",
        content_hash=canonical_sha256(report),
        artifact_path=artifact_path,
        metadata_json=json.dumps({"run_id": payload.run_id, "query": query}),
    )
    return {
        "artifact_id": artifact_id,
        "artifact_path": _portable_ref_path(state, artifact_path),
        "relative_path": _portable_ref_path(state, artifact_path),
        "report": report,
    }


@router.post("/tuning/handover")
def tuning_handover(payload: TuningHandoverRequest, state: AppState = Depends(get_state)) -> dict:
    request_id = str(payload.request_id or "").strip() or None
    run_id = str(payload.run_id or "").strip() or None
    if not request_id and not run_id:
        raise HTTPException(status_code=400, detail="request_id or run_id is required")
    if run_id and not request_id:
        row = state.db.fetchone("SELECT request_id FROM runs WHERE run_id = ?", (run_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        request_id = str(row["request_id"])
    assert request_id is not None
    req_row = state.db.fetchone(
        "SELECT request_id, status, created_at, user_input, request_type FROM requests WHERE request_id = ?",
        (request_id,),
    )
    if req_row is None:
        raise HTTPException(status_code=404, detail="request not found")

    run_rows = state.db.fetchall(
        """
        SELECT run_id, pod_id, status, created_at, latency_ms, attempt_count, winner_attempt_num
        FROM runs
        WHERE request_id = ?
        ORDER BY created_at
        """,
        (request_id,),
    )
    if run_id:
        run_rows = [r for r in run_rows if str(r["run_id"]) == run_id]
        if not run_rows:
            raise HTTPException(status_code=404, detail="run not found for request")

    max_attempts = max(1, min(int(payload.max_attempts), 200))
    attempts_left = max_attempts
    runs: list[dict[str, Any]] = []
    total_attempts = 0
    total_passed = 0
    total_tool_calls = 0
    failure_codes: dict[str, int] = defaultdict(int)
    for run in run_rows:
        this_run_id = str(run["run_id"])
        attempt_rows = state.db.fetchall(
            """
            SELECT attempt_id, attempt_num, persona_id, persona_version, pass, latency_ms,
                   artifact_executor_path, artifact_judge_path, inference_params_json, token_counts_json, failures_json
            FROM run_attempts
            WHERE run_id = ?
            ORDER BY attempt_num
            """,
            (this_run_id,),
        )
        if attempts_left <= 0:
            attempt_rows = []
        elif len(attempt_rows) > attempts_left:
            attempt_rows = attempt_rows[:attempts_left]
        attempts_left -= len(attempt_rows)

        attempts_payload: list[dict[str, Any]] = []
        for att in attempt_rows:
            total_attempts += 1
            passed = bool(att["pass"])
            if passed:
                total_passed += 1
            executor_payload = _safe_load_artifact_payload(state, str(att["artifact_executor_path"] or ""))
            judge_payload = _safe_load_artifact_payload(state, str(att["artifact_judge_path"] or ""))
            tool_count = len((executor_payload or {}).get("tool_calls") or [])
            total_tool_calls += tool_count
            failures = (judge_payload or {}).get("failures") or []
            for failure in failures:
                if isinstance(failure, dict):
                    code = str(failure.get("code") or "UNKNOWN")
                    failure_codes[code] += 1
            runtime = (executor_payload or {}).get("runtime") or {}
            response = ((executor_payload or {}).get("response") or {}).get("content")
            attempt_data: dict[str, Any] = {
                "attempt_id": str(att["attempt_id"]),
                "attempt_num": int(att["attempt_num"]),
                "persona_id": str(att["persona_id"]),
                "persona_version": str(att["persona_version"]),
                "pass": passed,
                "latency_ms": int(att["latency_ms"] or 0),
                "tool_calls_count": tool_count,
                "runtime": {
                    "provider": runtime.get("provider"),
                    "model": runtime.get("model"),
                    "model_digest": runtime.get("model_digest"),
                    "model_base_url": runtime.get("model_base_url"),
                    "inference_params": runtime.get("inference_params"),
                    "error": runtime.get("error"),
                },
                "response_excerpt": (str(response)[:300] if response is not None else ""),
                "judge_failures": failures,
            }
            if payload.include_payloads:
                attempt_data["executor_payload"] = executor_payload
                attempt_data["judge_payload"] = judge_payload
            attempts_payload.append(attempt_data)
        runs.append(
            {
                "run_id": this_run_id,
                "pod_id": str(run["pod_id"]),
                "status": str(run["status"]),
                "created_at": str(run["created_at"]),
                "latency_ms": int(run["latency_ms"] or 0),
                "attempt_count": int(run["attempt_count"] or 0),
                "winner_attempt_num": int(run["winner_attempt_num"] or 0),
                "attempts": attempts_payload,
            }
        )
        if attempts_left <= 0:
            break

    recommendations: list[str] = []
    if failure_codes.get("RUNTIME_ERROR", 0) > 0:
        recommendations.append("stabilize_model_inference: increase timeout or reduce max_tokens/context for this model rail")
    if failure_codes.get("WORKFLOW_TOOL_MISSING", 0) > 0:
        recommendations.append("improve_tool_call_emission: add stronger few-shot JSON tool-call exemplars for workflow personas")
    if total_tool_calls == 0 and str(req_row["request_type"]) in {"coding", "web_service", "auto"}:
        recommendations.append("enforce_actionability: require at least one allowed tool call for build-oriented workflows")
    if not recommendations:
        recommendations.append("no_critical_gaps_detected")

    handover = {
        "handover_id": f"th_{uuid.uuid4().hex[:12]}",
        "created_at": _now_iso(),
        "scope": {"request_id": request_id, "run_id": run_id},
        "request": {
            "request_id": str(req_row["request_id"]),
            "status": str(req_row["status"]),
            "created_at": str(req_row["created_at"]),
            "user_input": str(req_row["user_input"]),
            "request_type": str(req_row["request_type"]),
        },
        "summary": {
            "runs_analyzed": len(runs),
            "attempts_analyzed": total_attempts,
            "attempt_pass_rate": (round(total_passed / total_attempts, 4) if total_attempts else 0.0),
            "total_tool_calls": total_tool_calls,
            "failure_code_counts": dict(sorted(failure_codes.items())),
        },
        "runs": runs,
        "recommended_deltas": recommendations,
    }
    artifact_id, artifact_path = state.router_artifact_store.put_json(handover)
    state.db.insert_artifact_registry(
        artifact_id=artifact_id,
        created_at=_now_iso(),
        artifact_type="tuning_handover",
        content_hash=canonical_sha256(handover),
        artifact_path=artifact_path,
        metadata_json=json.dumps({"request_id": request_id, "run_id": run_id}),
    )
    return {
        "artifact_id": artifact_id,
        "artifact_path": _portable_ref_path(state, artifact_path),
        "relative_path": _portable_ref_path(state, artifact_path),
        "handover": handover,
    }


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, state: AppState = Depends(get_state)) -> dict:
    row = state.db.fetchone(
        "SELECT artifact_type, content_hash, artifact_path, metadata_json, created_at FROM artifacts WHERE artifact_id = ?",
        (artifact_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    artifact_path = str(row["artifact_path"])
    resolved_path = _resolve_data_path(state, artifact_path)
    if not resolved_path.exists():
        raise HTTPException(status_code=404, detail="artifact file not found")
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to parse artifact payload: {exc}") from exc
    metadata = json.loads(str(row["metadata_json"] or "{}"))
    return {
        "artifact_id": artifact_id,
        "artifact_type": str(row["artifact_type"]),
        "content_hash": str(row["content_hash"]),
        "artifact_path": _portable_ref_path(state, artifact_path),
        "relative_path": _portable_ref_path(state, artifact_path),
        "created_at": str(row["created_at"]),
        "metadata": metadata,
        "payload": payload,
    }
