import pytest

from core.executor.prompts import build_executor_prompt, render_executor_prompt
from core.executor.sandbox import (
    assert_no_forbidden_executor_payload,
    assert_no_survival_awareness,
    build_safe_retry_message,
)
from core.pod.dna import seed_dna
from core.snapshot.builder import build_snapshot
from core.snapshot.schema import SnapshotPolicies


def test_reward_firewall_blocks_survival_awareness() -> None:
    with pytest.raises(ValueError):
        assert_no_survival_awareness("This executor survived due to reward")


def test_judge_outputs_not_in_executor_prompt() -> None:
    dna = seed_dna()
    policies = SnapshotPolicies(
        tool_policy_id="tp_test",
        allowed_tools=["http_get"],
        forbidden_tools=["shell_exec"],
        budgets={"max_total_tool_calls": 1, "max_http_get": 1},
    )
    snapshot = build_snapshot(
        request_id="req_firewall",
        user_input="respond safely",
        request_type="general",
        policies=policies,
    )
    prompt = render_executor_prompt(dna, snapshot)
    forbidden_tokens = ["judge_result", "scores", "selection", "routing weight"]
    assert all(tok not in prompt.lower() for tok in forbidden_tokens)


def test_executor_prompt_payload_is_whitelist_only() -> None:
    dna = seed_dna()
    policies = SnapshotPolicies(
        tool_policy_id="tp_test",
        allowed_tools=["http_get"],
        forbidden_tools=["shell_exec"],
        budgets={"max_total_tool_calls": 1, "max_http_get": 1},
    )
    snapshot = build_snapshot(
        request_id="req_firewall_2",
        user_input="respond safely",
        request_type="general",
        policies=policies,
    )
    payload = build_executor_prompt(dna, snapshot)
    assert_no_forbidden_executor_payload(payload.__dict__)
    assert "lineage" not in payload.to_prompt_text().lower()
    assert "judge_result" not in payload.to_prompt_text().lower()


def test_firewall_allows_value_text_containing_failures_word() -> None:
    payload = {
        "context": {
            "state": {
                "workflow_projection": {
                    "execution": {"persona_mission": "report actionable failures with direct repair signals"}
                }
            }
        }
    }
    assert_no_forbidden_executor_payload(payload)


def test_safe_retry_messages_are_restricted() -> None:
    assert build_safe_retry_message("schema").startswith("Your output did not validate")
    assert build_safe_retry_message("tool_policy").startswith("You attempted a forbidden tool")
    assert "<= 2" in build_safe_retry_message("budget", max_tool_calls=2)
    with pytest.raises(ValueError):
        build_safe_retry_message("scores")
