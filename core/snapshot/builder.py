from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from core.snapshot.schema import (
    Snapshot,
    SnapshotContext,
    SnapshotPolicies,
    SnapshotRedaction,
    SnapshotRequest,
)


def _snapshot_id(request_id: str, user_input: str) -> str:
    fp = hashlib.sha256(f"{request_id}:{user_input}".encode("utf-8")).hexdigest()[:12]
    return f"snap_{fp}"


def _redact_input(text: str) -> str:
    return text.replace("api_key", "[REDACTED]")


def build_snapshot(
    *,
    request_id: str,
    user_input: str,
    request_type: str = "general",
    policies: SnapshotPolicies,
    context_state: Optional[Dict[str, Any]] = None,
) -> Snapshot:
    clean_input = _redact_input(user_input)
    return Snapshot(
        snapshot_id=_snapshot_id(request_id, clean_input),
        request_id=request_id,
        request=SnapshotRequest(user_input=clean_input, request_type=request_type),
        context=SnapshotContext(retrieved_items=[], state=context_state or {}),
        policies=policies,
        redaction=SnapshotRedaction(applied=True, notes=["basic-string-redaction"]),
    )
