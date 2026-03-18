from pathlib import Path
from typing import Any

from core.executor.runner import ExecutorRunner
from core.pod.pod import init_default_pods
from core.storage.db import Database


def test_ask_me_prompt_returns_question_not_refusal(tmp_path: Path) -> None:
    db = Database(tmp_path / "intent.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")

    result = pods["pod_a"].run_request(
        request_id="req_intent_ask_me",
        user_input="ask me something you genuinely dont have an answer for",
        request_type="auto",
    )
    content = str(result["executor_output"]["response"]["content"])
    assert "?" in content
    assert "don't have an answer" not in content.lower()
    assert result["judge_result"]["pass"] is True


class PreflightAwareBackend:
    def generate(self, *, run_id: str, dna: Any, snapshot: Any) -> dict[str, Any]:
        state = (snapshot.context.state or {}) if snapshot.context else {}
        if state.get("preflight_instruction"):
            content = "What's a belief you hold strongly that your younger self would disagree with?"
        else:
            content = "I don't have an answer to that."
        return {
            "executor_output_id": f"xo_{run_id[-12:]}",
            "run_id": run_id,
            "response": {"type": "final", "content": content},
            "plan": [{"step": 1, "intent": "Answer request"}],
            "tool_calls": [],
            "trace": {"summary": "test backend", "signals": {"uncertainty": "low", "assumptions": []}},
        }


def test_ask_me_preflight_instruction_avoids_retry(tmp_path: Path) -> None:
    db = Database(tmp_path / "intent_preflight.db")
    repo_root = Path(__file__).resolve().parents[1]
    db.migrate(repo_root / "core" / "storage" / "migrations.sql")
    pods = init_default_pods(db, tmp_path / "artifacts")
    pod = pods["pod_a"]
    pod.executor = ExecutorRunner(backend=PreflightAwareBackend())

    result = pod.run_request(
        request_id="req_intent_preflight",
        user_input="ask me something you genuinely dont have an answer for",
        request_type="auto",
    )
    content = str(result["executor_output"]["response"]["content"])
    assert "?" in content
    assert "don't have an answer" not in content.lower()
    assert result["judge_result"]["pass"] is True
    assert result["attempt_count"] == 1
