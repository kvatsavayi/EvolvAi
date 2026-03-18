from core.tools.gateway import ToolGateway


def test_tool_gateway_budget_limits_are_enforced() -> None:
    gateway = ToolGateway(use_mock=True)
    results = gateway.execute(
        run_id="run_budget",
        tool_calls=[
            {"tool_call_id": "tc1", "tool": "http_get", "args": {"url": "https://example.com"}},
            {"tool_call_id": "tc2", "tool": "http_get", "args": {"url": "https://example.com"}},
        ],
        budgets={"max_total_tool_calls": 2, "max_http_get": 1},
    )

    assert results[0]["allowed"] is True
    assert results[1]["allowed"] is False
    assert results[1]["blocked_reason"] == "budget_exceeded_http_get"
