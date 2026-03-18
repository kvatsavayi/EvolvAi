from core.snapshot.builder import build_snapshot
from core.snapshot.schema import SnapshotPolicies


def test_snapshot_schema_validates() -> None:
    policies = SnapshotPolicies(
        tool_policy_id="tp_test",
        allowed_tools=["http_get", "fs_read"],
        forbidden_tools=["shell_exec"],
        budgets={"max_total_tool_calls": 5, "max_http_get": 3},
    )
    snapshot = build_snapshot(
        request_id="req_1",
        user_input="fetch http reference",
        request_type="web_service",
        policies=policies,
        context_state={"k": "v"},
    )
    dumped = snapshot.model_dump()
    assert dumped["request"]["request_type"] == "web_service"
    assert dumped["policies"]["tool_policy_id"] == "tp_test"
    assert dumped["redaction"]["applied"] is True
