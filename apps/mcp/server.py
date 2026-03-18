from __future__ import annotations

import json
import sys
from typing import Any

from apps.api.dependencies import get_state
from apps.api.models import (
    RouterRequest,
    TuningHandoverRequest,
    WorkflowRequest,
    WorkspaceFileWriteRequest,
)
from apps.api.routes import (
    get_artifact,
    get_request,
    get_workspace,
    route_persona,
    run_workflow,
    tuning_handover,
    workspace_read_file,
    workspace_write_file,
)


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _read_message() -> dict[str, Any] | None:
    content_length: int | None = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        decoded = line.decode("utf-8").strip()
        if decoded == "":
            break
        key, _, value = decoded.partition(":")
        if key.lower() == "content-length":
            content_length = int(value.strip())
    if content_length is None:
        return None
    raw = sys.stdin.buffer.read(content_length)
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _jsonrpc_ok(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _jsonrpc_err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_list() -> list[dict[str, Any]]:
    return [
        {
            "name": "workflow.run",
            "description": "Run persona workflow orchestration for a user request.",
            "inputSchema": {
                "type": "object",
                "required": ["user_input"],
                "properties": {
                    "user_input": {"type": "string"},
                    "request_type": {"type": "string"},
                    "max_steps": {"type": "integer"},
                    "retry_same_persona_once": {"type": "boolean"},
                    "canonical_target": {"type": "string"},
                },
            },
        },
        {
            "name": "router.route",
            "description": "Route a request to a persona and return handoff payload.",
            "inputSchema": {
                "type": "object",
                "required": ["user_input"],
                "properties": {
                    "user_input": {"type": "string"},
                    "allowed_personas": {"type": "array", "items": {"type": "string"}},
                    "workspace_refs": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        {
            "name": "request.status",
            "description": "Get request status with optional run/attempt details.",
            "inputSchema": {
                "type": "object",
                "required": ["request_id"],
                "properties": {
                    "request_id": {"type": "string"},
                    "include_run_details": {"type": "boolean"},
                    "include_attempts": {"type": "boolean"},
                },
            },
        },
        {
            "name": "workspace.read_file",
            "description": "Read a file from a workspace.",
            "inputSchema": {
                "type": "object",
                "required": ["workspace_id", "path"],
                "properties": {"workspace_id": {"type": "string"}, "path": {"type": "string"}},
            },
        },
        {
            "name": "workspace.write_file",
            "description": "Write a file into a workspace under a run ownership check.",
            "inputSchema": {
                "type": "object",
                "required": ["workspace_id", "run_id", "path", "content"],
                "properties": {
                    "workspace_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
        {
            "name": "artifact.get",
            "description": "Fetch artifact payload by artifact_id.",
            "inputSchema": {
                "type": "object",
                "required": ["artifact_id"],
                "properties": {"artifact_id": {"type": "string"}},
            },
        },
        {
            "name": "tuning.handover",
            "description": "Build replayable tuning handover artifact for request or run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "run_id": {"type": "string"},
                    "max_attempts": {"type": "integer"},
                    "include_payloads": {"type": "boolean"},
                },
            },
        },
    ]


def _resource_list() -> list[dict[str, Any]]:
    return [
        {"uri": "agentpods://requests/latest", "name": "Latest Request"},
        {"uri": "agentpods://runs/latest", "name": "Latest Run"},
        {"uri": "agentpods://artifacts/latest", "name": "Latest Artifacts"},
    ]


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    state = get_state()
    if name == "workflow.run":
        payload = WorkflowRequest(
            user_input=str(arguments.get("user_input", "")),
            request_type=str(arguments.get("request_type", "general")),
            max_steps=int(arguments.get("max_steps", 4)),
            retry_same_persona_once=bool(arguments.get("retry_same_persona_once", True)),
            canonical_target=(str(arguments["canonical_target"]) if arguments.get("canonical_target") else None),
        )
        return run_workflow(payload=payload, state=state)
    if name == "router.route":
        payload = RouterRequest(
            user_input=str(arguments.get("user_input", "")),
            allowed_personas=[str(x) for x in (arguments.get("allowed_personas") or [])],
            workspace_refs=[str(x) for x in (arguments.get("workspace_refs") or [])],
        )
        return route_persona(payload=payload, state=state)
    if name == "request.status":
        return get_request(
            request_id=str(arguments.get("request_id", "")),
            include_run_details=bool(arguments.get("include_run_details", True)),
            include_attempts=bool(arguments.get("include_attempts", True)),
            state=state,
        ).model_dump()
    if name == "workspace.read_file":
        return workspace_read_file(
            workspace_id=str(arguments.get("workspace_id", "")),
            file_path=str(arguments.get("path", "")),
            state=state,
        )
    if name == "workspace.write_file":
        return workspace_write_file(
            workspace_id=str(arguments.get("workspace_id", "")),
            payload=WorkspaceFileWriteRequest(
                run_id=str(arguments.get("run_id", "")),
                path=str(arguments.get("path", "")),
                content=str(arguments.get("content", "")),
            ),
            state=state,
        )
    if name == "artifact.get":
        return get_artifact(artifact_id=str(arguments.get("artifact_id", "")), state=state)
    if name == "tuning.handover":
        payload = TuningHandoverRequest(
            request_id=(str(arguments["request_id"]) if arguments.get("request_id") else None),
            run_id=(str(arguments["run_id"]) if arguments.get("run_id") else None),
            max_attempts=int(arguments.get("max_attempts", 20)),
            include_payloads=bool(arguments.get("include_payloads", False)),
        )
        return tuning_handover(payload=payload, state=state)
    raise ValueError(f"unknown tool: {name}")


def _read_resource(uri: str) -> dict[str, Any]:
    state = get_state()
    if uri == "agentpods://requests/latest":
        row = state.db.fetchone("SELECT request_id FROM requests ORDER BY created_at DESC LIMIT 1", ())
        if row is None:
            return {"uri": uri, "contents": [{"mimeType": "application/json", "text": "{}"}]}
        payload = get_request(str(row["request_id"]), include_run_details=True, include_attempts=True, state=state).model_dump()
        return {"uri": uri, "contents": [{"mimeType": "application/json", "text": json.dumps(payload)}]}
    if uri == "agentpods://runs/latest":
        row = state.db.fetchone(
            """
            SELECT run_id, request_id, status, pod_id, created_at
            FROM runs ORDER BY created_at DESC LIMIT 1
            """,
            (),
        )
        payload = dict(row) if row is not None else {}
        return {"uri": uri, "contents": [{"mimeType": "application/json", "text": json.dumps(payload)}]}
    if uri == "agentpods://artifacts/latest":
        rows = state.db.fetchall(
            "SELECT artifact_id, artifact_type, artifact_path, created_at FROM artifacts ORDER BY created_at DESC LIMIT 20",
            (),
        )
        payload = [dict(r) for r in rows]
        return {"uri": uri, "contents": [{"mimeType": "application/json", "text": json.dumps(payload)}]}
    if uri.startswith("agentpods://artifact/"):
        artifact_id = uri.split("agentpods://artifact/", 1)[1]
        payload = get_artifact(artifact_id=artifact_id, state=state)
        return {"uri": uri, "contents": [{"mimeType": "application/json", "text": json.dumps(payload)}]}
    if uri.startswith("agentpods://workspace/"):
        workspace_id = uri.split("agentpods://workspace/", 1)[1]
        payload = get_workspace(workspace_id=workspace_id, state=state)
        return {"uri": uri, "contents": [{"mimeType": "application/json", "text": json.dumps(payload)}]}
    raise ValueError(f"unknown resource: {uri}")


def serve() -> None:
    while True:
        request = _read_message()
        if request is None:
            break
        request_id = request.get("id")
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "agent-pods-mcp", "version": "0.1.0"},
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                }
                _write_message(_jsonrpc_ok(request_id, result))
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                _write_message(_jsonrpc_ok(request_id, {"tools": _tool_list()}))
            elif method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                result = _call_tool(name=name, arguments=arguments if isinstance(arguments, dict) else {})
                _write_message(_jsonrpc_ok(request_id, {"content": [{"type": "text", "text": json.dumps(result)}]}))
            elif method == "resources/list":
                _write_message(_jsonrpc_ok(request_id, {"resources": _resource_list()}))
            elif method == "resources/read":
                uri = str(params.get("uri") or "")
                _write_message(_jsonrpc_ok(request_id, _read_resource(uri)))
            else:
                _write_message(_jsonrpc_err(request_id, -32601, f"Method not found: {method}"))
        except Exception as exc:
            _write_message(_jsonrpc_err(request_id, -32000, str(exc)))


if __name__ == "__main__":
    serve()

