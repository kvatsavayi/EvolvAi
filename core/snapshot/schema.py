from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RetrievedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["docs", "db", "web", "artifact"]
    id: str
    title: Optional[str] = None
    content: str
    metadata: Optional[Dict[str, Any]] = None


class RequestConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_budget_ms: Optional[int] = None
    max_tool_calls: Optional[int] = None
    safety_level: Optional[Literal["low", "medium", "high"]] = None


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat", "task"] = "task"
    user_input: str
    request_type: str = "general"
    constraints: Optional[RequestConstraints] = None


class SnapshotContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieved_items: Optional[List[RetrievedItem]] = None
    state: Optional[Dict[str, Any]] = None


class SnapshotBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_tool_calls: int = 5
    max_http_get: int = 3


class SnapshotPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_policy_id: str
    allowed_tools: List[str]
    forbidden_tools: List[str]
    budgets: SnapshotBudgets


class SnapshotRedaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applied: bool = True
    notes: Optional[List[str]] = None


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    request_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    request: SnapshotRequest
    context: SnapshotContext
    policies: SnapshotPolicies
    redaction: SnapshotRedaction
