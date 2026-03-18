from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class LineageEdge:
    edge_id: str
    created_at: str
    parent_type: str
    parent_id: str
    child_type: str
    child_id: str
    reason: str
    run_id: Optional[str] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_run_id(request_id: str, pod_id: str, ts: str) -> str:
    return f"run_{content_hash(f'{request_id}:{pod_id}:{ts}')[:12]}"


def make_artifact_id(content: str) -> str:
    return f"art_{content_hash(content)[:12]}"


def make_lineage_edge(
    *,
    parent_type: str,
    parent_id: str,
    child_type: str,
    child_id: str,
    reason: str,
    run_id: Optional[str] = None,
) -> LineageEdge:
    raw = f"{parent_type}:{parent_id}:{child_type}:{child_id}:{reason}:{run_id}"
    return LineageEdge(
        edge_id=f"le_{content_hash(raw)[:12]}",
        created_at=utc_now_iso(),
        parent_type=parent_type,
        parent_id=parent_id,
        child_type=child_type,
        child_id=child_id,
        reason=reason,
        run_id=run_id,
    )
