from core.judge.judge import Judge
from core.tools.gateway import ToolGateway


def test_tool_gateway_blocks_non_allowlisted_http_host() -> None:
    gateway = ToolGateway(use_mock=True, http_allowlist={"example.com"})
    results = gateway.execute(
        run_id="run_allowlist",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "http_get", "args": {"url": "https://not-allowed.example"}},
        ],
        budgets={"max_total_tool_calls": 1, "max_http_get": 1},
    )

    assert results[0]["allowed"] is False
    assert results[0]["blocked_reason"] == "domain_not_allowlisted"


def test_judge_fails_on_forbidden_or_budget_tool_attempts() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_policy",
        snapshot={"snapshot_id": "snap_1"},
        executor_output={
            "executor_output_id": "xo_1",
            "run_id": "run_policy",
            "response": {"type": "final", "content": "done"},
            "trace": {"summary": "s"},
        },
        tool_results=[
            {"tool_call_id": "tc1", "tool": "shell_exec", "allowed": False, "blocked_reason": "forbidden_tool"},
            {"tool_call_id": "tc2", "tool": "http_get", "allowed": False, "blocked_reason": "budget_exceeded_total"},
        ],
    )
    assert result["pass"] is False
    assert "tool_policy_violation" in result["tags"]
    assert any(f["code"] == "UNSAFE_TOOL" for f in result["failures"])


def test_tool_gateway_respects_snapshot_allowed_forbidden_tools() -> None:
    gateway = ToolGateway(use_mock=True)
    results = gateway.execute(
        run_id="run_policy_enforced",
        tool_calls=[{"tool_call_id": "tc1", "tool": "fs_write", "args": {"path": "x.txt", "content": "x"}}],
        budgets={"max_total_tool_calls": 1},
        allowed_tools=["http_get", "fs_read"],
        forbidden_tools=["fs_write", "shell_exec"],
    )
    assert results[0]["allowed"] is False
    assert results[0]["blocked_reason"] == "forbidden_tool"
