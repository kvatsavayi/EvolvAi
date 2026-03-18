from apps.api.routes import _winner_key


def test_winner_key_penalizes_retries_when_other_signals_match() -> None:
    base = {
        "judge_result": {"pass": True, "scores": {"policy_compliance": 1.0}},
        "tool_results": [],
        "latency_ms": 1000,
    }
    one_attempt = dict(base, attempt_count=1)
    two_attempts = dict(base, attempt_count=2)
    assert _winner_key(one_attempt) > _winner_key(two_attempts)
