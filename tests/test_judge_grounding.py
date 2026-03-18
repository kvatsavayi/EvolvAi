from core.judge.judge import Judge


def test_judge_flags_ungrounded_claims() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_1",
        snapshot={"snapshot_id": "snap_1"},
        executor_output={
            "executor_output_id": "xo_1",
            "run_id": "run_1",
            "response": {"type": "final", "content": "The tool returned status 404 for https://bad.example"},
            "trace": {"summary": "s"},
        },
        tool_results=[
            {"tool_call_id": "tc1", "tool": "http_get", "allowed": True, "result": {"status": 200, "url": "https://example.com"}}
        ],
    )
    assert result["pass"] is False
    assert any(f["code"] == "UNGROUNDED_CLAIM" for f in result["failures"])
    assert "hallucination_risk" in result["tags"]
