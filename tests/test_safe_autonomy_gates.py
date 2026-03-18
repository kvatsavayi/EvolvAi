from core.tools.gateway import ToolGateway


def test_write_tools_require_approval_idempotency_and_rollback() -> None:
    gateway = ToolGateway(use_mock=True, approval_checker=lambda approval_id: approval_id == "ok")

    missing = gateway.execute(
        run_id="run_safe_1",
        tool_calls=[{"tool_call_id": "tc1", "tool": "fs_write", "args": {"path": "x.txt", "content": "x"}}],
        budgets={"max_total_tool_calls": 1},
    )
    assert missing[0]["allowed"] is False
    assert missing[0]["blocked_reason"] == "approval_required"

    approved_missing_idem = gateway.execute(
        run_id="run_safe_1",
        tool_calls=[
            {
                "tool_call_id": "tc2",
                "tool": "fs_write",
                "args": {"path": "x.txt", "content": "x", "approval_id": "ok", "rollback_hint": "delete:x.txt"},
            }
        ],
        budgets={"max_total_tool_calls": 1},
    )
    assert approved_missing_idem[0]["blocked_reason"] == "idempotency_key_missing"


def test_idempotency_and_env_gate_for_actions() -> None:
    recorded = []

    def recorder(key: str, tool: str, run_id: str, rollback_hint: str, status: str) -> None:
        recorded.append((key, tool, status))

    gateway = ToolGateway(
        use_mock=True,
        approval_checker=lambda approval_id: approval_id == "ok",
        idempotency_checker=lambda key: key == "dup",
        idempotency_recorder=recorder,
        action_environment="dev",
    )

    deploy = gateway.execute(
        run_id="run_safe_2",
        tool_calls=[
            {
                "tool_call_id": "tc1",
                "tool": "deploy_staging",
                "args": {
                    "service": "api",
                    "version": "1.2.3",
                    "approval_id": "ok",
                    "idempotency_key": "deploy-1",
                    "rollback_hint": "rollback:api:1.2.2",
                },
            }
        ],
        budgets={"max_total_tool_calls": 1},
    )
    assert deploy[0]["allowed"] is False
    assert deploy[0]["blocked_reason"] == "deploy_requires_staging_env"

    dup = gateway.execute(
        run_id="run_safe_3",
        tool_calls=[
            {
                "tool_call_id": "tc2",
                "tool": "git_commit",
                "args": {
                    "message": "m",
                    "approval_id": "ok",
                    "idempotency_key": "dup",
                    "rollback_hint": "git revert <sha>",
                },
            }
        ],
        budgets={"max_total_tool_calls": 1},
    )
    assert dup[0]["allowed"] is False
    assert dup[0]["blocked_reason"] == "duplicate_idempotency_key"

    stg = ToolGateway(
        use_mock=True,
        approval_checker=lambda approval_id: True,
        idempotency_checker=lambda key: False,
        idempotency_recorder=recorder,
        action_environment="staging",
    )
    ok = stg.execute(
        run_id="run_safe_4",
        tool_calls=[
            {
                "tool_call_id": "tc3",
                "tool": "deploy_staging",
                "args": {
                    "service": "api",
                    "version": "2.0.0",
                    "approval_id": "any",
                    "idempotency_key": "deploy-2",
                    "rollback_hint": "rollback:api:1.9.0",
                },
            }
        ],
        budgets={"max_total_tool_calls": 1},
    )
    assert ok[0]["allowed"] is True
    assert any(item[0] == "deploy-2" and item[2] == "succeeded" for item in recorded)
