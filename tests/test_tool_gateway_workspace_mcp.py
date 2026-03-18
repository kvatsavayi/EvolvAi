from __future__ import annotations

from pathlib import Path

from core.tools.gateway import ToolGateway


def test_workspace_mcp_read_write_search_with_provenance(tmp_path: Path) -> None:
    gateway = ToolGateway(use_mock=True, sandbox_dir=tmp_path)
    results = gateway.execute(
        run_id="run_ws_1",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "workspace_write", "args": {"path": "src/a.txt", "content": "hello world"}},
            {"tool_call_id": "tc2", "tool": "workspace_read", "args": {"path": "src/a.txt", "max_bytes": 1024}},
            {"tool_call_id": "tc3", "tool": "workspace_search", "args": {"path": "src", "query": "world", "max_hits": 10}},
            {"tool_call_id": "tc4", "tool": "workspace_list", "args": {"path": "src", "glob": "*.txt"}},
        ],
        budgets={"max_total_tool_calls": 10},
        allowed_tools=["workspace_write", "workspace_read", "workspace_search", "workspace_list"],
        forbidden_tools=[],
        active_persona_id="implementation",
    )
    assert all(r["allowed"] for r in results)
    assert results[0]["result"]["bytes_written"] == len("hello world".encode("utf-8"))
    assert results[1]["result"]["content"] == "hello world"
    assert len(results[2]["result"]["hits"]) >= 1
    assert len(results[3]["result"]["items"]) == 1
    for r in results:
        prov = (r.get("result") or {}).get("provenance")
        assert isinstance(prov, dict)
        assert str(prov.get("input_hash", "")).strip() != ""
        assert str(prov.get("output_hash", "")).strip() != ""


def test_workspace_write_requires_implementation_persona(tmp_path: Path) -> None:
    gateway = ToolGateway(use_mock=True, sandbox_dir=tmp_path)
    results = gateway.execute(
        run_id="run_ws_2",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "workspace_write", "args": {"path": "a.txt", "content": "x"}},
        ],
        budgets={"max_total_tool_calls": 2},
        allowed_tools=["workspace_write"],
        forbidden_tools=[],
        active_persona_id="research",
    )
    assert results[0]["allowed"] is False
    assert results[0]["blocked_reason"] == "persona_forbidden_tool"


def test_workspace_run_requires_qa_and_allowlisted_command(tmp_path: Path) -> None:
    gateway = ToolGateway(use_mock=True, sandbox_dir=tmp_path)

    blocked_by_persona = gateway.execute(
        run_id="run_ws_3",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "workspace_run", "args": {"cmd": "python3 -m pytest --version", "timeout_s": 30}},
        ],
        budgets={"max_total_tool_calls": 2},
        allowed_tools=["workspace_run"],
        forbidden_tools=[],
        active_persona_id="implementation",
    )
    assert blocked_by_persona[0]["allowed"] is False
    assert blocked_by_persona[0]["blocked_reason"] == "workspace_run_requires_qa_test"

    blocked_cmd = gateway.execute(
        run_id="run_ws_4",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "workspace_run", "args": {"cmd": "ls", "timeout_s": 30}},
        ],
        budgets={"max_total_tool_calls": 2},
        allowed_tools=["workspace_run"],
        forbidden_tools=[],
        active_persona_id="qa_test",
    )
    assert blocked_cmd[0]["allowed"] is True
    assert (blocked_cmd[0].get("result") or {}).get("cmd") == "python3 -m pytest tests -q"

    allowed = gateway.execute(
        run_id="run_ws_5",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "workspace_run", "args": {"cmd": "python3 -m pytest --version", "timeout_s": 30}},
        ],
        budgets={"max_total_tool_calls": 2},
        allowed_tools=["workspace_run"],
        forbidden_tools=[],
        active_persona_id="qa_test",
    )
    assert allowed[0]["allowed"] is True
    assert "exit_code" in (allowed[0].get("result") or {})
    assert (allowed[0].get("result") or {}).get("cmd") == "python3 -m pytest tests -q"


def test_tool_aliases_and_read_write_byte_budgets(tmp_path: Path) -> None:
    kb = tmp_path / "data" / "kb"
    kb.mkdir(parents=True, exist_ok=True)
    (kb / "doc.txt").write_text("alpha beta gamma", encoding="utf-8")

    gateway = ToolGateway(use_mock=True, sandbox_dir=tmp_path)
    results = gateway.execute(
        run_id="run_ws_alias_1",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "write_file", "args": {"path": "data/workspaces/ws_1/a.txt", "content": "hello"}},
            {"tool_call_id": "tc2", "tool": "read_file", "args": {"path": "data/workspaces/ws_1/a.txt"}},
            {"tool_call_id": "tc3", "tool": "search_local_kb", "args": {"query": "beta", "max_hits": 5}},
        ],
        budgets={"max_total_tool_calls": 5, "max_reads": 2, "max_writes": 1, "max_bytes": 100},
        allowed_tools=["write_file", "read_file", "search_local_kb"],
        forbidden_tools=[],
        active_persona_id="implementation",
    )
    assert results[0]["allowed"] is True
    assert results[1]["allowed"] is True
    assert results[2]["allowed"] is True
    assert len((results[2].get("result") or {}).get("hits") or []) >= 1

    blocked = gateway.execute(
        run_id="run_ws_alias_2",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "write_file", "args": {"path": "data/workspaces/ws_1/b.txt", "content": "123456"}},
            {"tool_call_id": "tc2", "tool": "write_file", "args": {"path": "data/workspaces/ws_1/c.txt", "content": "123456"}},
        ],
        budgets={"max_total_tool_calls": 5, "max_reads": 2, "max_writes": 1, "max_bytes": 100},
        allowed_tools=["write_file"],
        forbidden_tools=[],
        active_persona_id="implementation",
    )
    assert blocked[0]["allowed"] is True
    assert blocked[1]["allowed"] is False
    assert blocked[1]["blocked_reason"] == "budget_exceeded_writes"


def test_write_file_normalizes_workspace_prefixed_test_module_path(tmp_path: Path) -> None:
    gateway = ToolGateway(use_mock=True, sandbox_dir=tmp_path)
    test_content = (
        "from pathlib import Path\n\n"
        "def _load_app_module():\n"
        "    p = Path('data/workspaces/ws_abc123/app/main.py')\n"
        "    return p\n"
    )
    out = gateway.execute(
        run_id="run_norm_1",
        tool_calls=[
            {
                "tool_call_id": "tc1",
                "tool": "write_file",
                "args": {"path": "data/workspaces/ws_1/tests/test_main.py", "content": test_content},
            }
        ],
        budgets={"max_total_tool_calls": 2, "max_writes": 2, "max_bytes": 10000},
        allowed_tools=["write_file"],
        forbidden_tools=[],
        active_persona_id="implementation",
    )
    assert out[0]["allowed"] is True
    norm = (out[0].get("result") or {}).get("normalization") or {}
    assert norm.get("rule") == "normalize_workspace_prefixed_app_main_path"
    written = (tmp_path / "data" / "workspaces" / "ws_1" / "tests" / "test_main.py").read_text(encoding="utf-8")
    assert "data/workspaces/ws_abc123/app/main.py" not in written
    assert "Path('app/main.py')" in written
