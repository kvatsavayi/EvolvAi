from __future__ import annotations

from typing import Any

from apps.api.models import WorkflowRequest
from core_runtime.contracts import ExecuteRequestPayload, ExecuteRequestResult, normalize_execute_result


def execute_request(payload: ExecuteRequestPayload | dict[str, Any], *, state: Any) -> ExecuteRequestResult:
    from apps.api.routes import _execute_workflow_request

    request = WorkflowRequest.model_validate(dict(payload))
    result = _execute_workflow_request(payload=request, state=state)
    return normalize_execute_result(result)
