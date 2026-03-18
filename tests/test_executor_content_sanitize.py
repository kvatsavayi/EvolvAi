from pathlib import Path
from typing import Any

from core.executor.runner import ExecutorRunner
from core.pod.dna import seed_dna
from core.pod.pod import init_default_pods
from core.snapshot.schema import Snapshot, SnapshotBudgets, SnapshotContext, SnapshotPolicies, SnapshotRedaction, SnapshotRequest
from core.storage.db import Database


class EchoSchemaBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": "**type: final** **content: Hello from clean content.**"},
            "plan": [{"step": 1, "intent": "answer"}],
            "tool_calls": [],
            "trace": {"summary": "echo schema", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def test_executor_sanitizes_schema_echo_content_before_persist(tmp_path: Path) -> None:
    db = Database(tmp_path / "sanitize.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")
    pod = pods["pod_a"]
    pod.executor = ExecutorRunner(backend=EchoSchemaBackend())

    result = pod.run_request(
        request_id="req_sanitize_echo",
        user_input="say hello",
        request_type="general",
    )
    content = str(result["executor_output"]["response"]["content"])
    assert content == "Hello from clean content."
    assert str(result["executor_output"]["response"]["content_raw"]).startswith("**type: final**")


class JsonContractBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        state = (snapshot.context.state or {}) if snapshot.context else {}
        ws = str(state.get("workspace_id") or "ws_test")
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {
                "type": "final",
                "content": (
                    "```json\n"
                    "{"
                    "\"response\":{\"type\":\"final\",\"content\":\"Implemented weather app.\"},"
                    "\"tool_calls\":[{\"tool\":\"write_file\",\"args\":{\"path\":\"data/workspaces/"
                    + ws
                    + "/app/main.py\",\"content\":\"print('ok')\"},\"reason\":\"create app\"}]"
                    "}\n```"
                ),
            },
            "plan": [{"step": 1, "intent": "answer"}],
            "tool_calls": [],
            "trace": {"summary": "json contract", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def test_executor_parses_model_json_contract_into_tool_calls() -> None:
    runner = ExecutorRunner(backend=JsonContractBackend())
    snapshot = Snapshot(
        snapshot_id="snap_json_parse",
        request_id="req_json_parse",
        request=SnapshotRequest(type="task", user_input="build weather app", request_type="coding"),
        context=SnapshotContext(state={"workflow_goal": "weather_station_app", "persona_id": "implementation", "workspace_id": "ws_abc"}),
        policies=SnapshotPolicies(
            tool_policy_id="tp_1",
            allowed_tools=["write_file", "read_file", "search_local_kb", "workspace_run"],
            forbidden_tools=[],
            budgets=SnapshotBudgets(max_total_tool_calls=5, max_http_get=0),
        ),
        redaction=SnapshotRedaction(applied=False, notes=[]),
    )
    out = runner.run(run_id="run_json_parse", dna=seed_dna(), snapshot=snapshot)
    assert out["response"]["content"] == "Implemented weather app."
    assert out["tool_calls"]
    first = out["tool_calls"][0]
    assert first["tool"] == "write_file"
    assert first["tool_call_id"] == "tc_model_1"


class NoToolCrudBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": "```json\n{\"note\":\"planning only\"}\n```"},
            "plan": [{"step": 1, "intent": "answer"}],
            "tool_calls": [],
            "trace": {"summary": "no tools", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def test_executor_forces_generic_workflow_fallback_tool_calls() -> None:
    runner = ExecutorRunner(backend=NoToolCrudBackend())
    snapshot = Snapshot(
        snapshot_id="snap_crud_fallback",
        request_id="req_crud_fallback",
        request=SnapshotRequest(type="task", user_input="build a simple CRUD application", request_type="coding"),
        context=SnapshotContext(state={"workflow_goal": "generic_build_app", "persona_id": "implementation", "workspace_id": "ws_crud"}),
        policies=SnapshotPolicies(
            tool_policy_id="tp_1",
            allowed_tools=["write_file", "read_file", "search_local_kb", "workspace_run"],
            forbidden_tools=[],
            budgets=SnapshotBudgets(max_total_tool_calls=6, max_http_get=0),
        ),
        redaction=SnapshotRedaction(applied=False, notes=[]),
    )
    out = runner.run(run_id="run_crud_fallback", dna=seed_dna(), snapshot=snapshot)
    calls = out.get("tool_calls") or []
    assert len(calls) >= 2
    assert calls[0]["tool"] == "write_file"
    assert str(calls[0]["args"]["path"]).endswith("data/workspaces/ws_crud/app/main.py")
    assert calls[1]["tool"] == "write_file"
    assert str(calls[1]["args"]["path"]).endswith("data/workspaces/ws_crud/tests/test_main.py")


class PythonDictContractBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {
                "type": "final",
                "content": (
                    "```json\n"
                    "{'response': {'type': 'final', 'content': 'Built CRUD app.'}, "
                    "'tool_calls': [{'tool': 'write_file', 'args': {'path': 'data/workspaces/ws_x/app/main.py', 'content': 'print(1)'}}]}"
                    "\n```"
                ),
            },
            "plan": [{"step": 1, "intent": "answer"}],
            "tool_calls": [],
            "trace": {"summary": "python dict contract", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def test_executor_parses_python_dict_style_contract() -> None:
    runner = ExecutorRunner(backend=PythonDictContractBackend())
    snapshot = Snapshot(
        snapshot_id="snap_py_dict_parse",
        request_id="req_py_dict_parse",
        request=SnapshotRequest(type="task", user_input="build crud app", request_type="coding"),
        context=SnapshotContext(state={"workflow_goal": "generic_build_app", "persona_id": "implementation", "workspace_id": "ws_x"}),
        policies=SnapshotPolicies(
            tool_policy_id="tp_1",
            allowed_tools=["write_file", "read_file", "search_local_kb", "workspace_run"],
            forbidden_tools=[],
            budgets=SnapshotBudgets(max_total_tool_calls=6, max_http_get=0),
        ),
        redaction=SnapshotRedaction(applied=False, notes=[]),
    )
    out = runner.run(run_id="run_py_dict_parse", dna=seed_dna(), snapshot=snapshot)
    assert out["response"]["content"] == "Built CRUD app."
    assert out["tool_calls"]
    assert out["tool_calls"][0]["tool"] == "write_file"
