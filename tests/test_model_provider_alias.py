from __future__ import annotations

from core.executor.runner import ExecutorRunner
from core.snapshot.builder import build_snapshot
from core.snapshot.schema import SnapshotPolicies


def test_openai_provider_alias_selects_remote_backend(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    runner = ExecutorRunner()
    assert runner.backend.__class__.__name__ == "RemoteModelBackend"


def test_chatgpt_provider_alias_selects_remote_backend(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "chatgpt")
    runner = ExecutorRunner()
    assert runner.backend.__class__.__name__ == "RemoteModelBackend"


def test_persona_scoped_provider_override(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_PROVIDER_RESEARCH", "openai")
    runner = ExecutorRunner()
    snapshot = build_snapshot(
        request_id="req_1",
        user_input="research first",
        request_type="coding",
        policies=SnapshotPolicies(
            tool_policy_id="tp_default",
            allowed_tools=["search_local_kb"],
            forbidden_tools=[],
            budgets={"max_total_tool_calls": 4, "max_http_get": 2},
        ),
        context_state={"persona_id": "research"},
    )
    rail = runner.model_rail(snapshot)
    assert str(rail.get("provider")) == "openai"
