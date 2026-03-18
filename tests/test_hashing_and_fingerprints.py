from core.observability.canonical import canonical_json_dumps, canonical_sha256
from core.observability.traces import behavior_fingerprint, trace_fingerprint, tool_sequence_fingerprint
from core.pod.dna import make_dna_id


def test_canonical_hash_stable_with_key_order() -> None:
    a = {"b": 1, "a": {"y": 2, "x": 3}}
    b = {"a": {"x": 3, "y": 2}, "b": 1}
    assert canonical_json_dumps(a) == canonical_json_dumps(b)
    assert canonical_sha256(a) == canonical_sha256(b)


def test_dna_id_stable_from_canonical_content() -> None:
    prompt_1 = {"instructions": "i", "system": "s", "output_contract": "o"}
    prompt_2 = {"output_contract": "o", "system": "s", "instructions": "i"}
    constraints = ["c1", "c2"]
    assert make_dna_id(prompt_1, constraints) == make_dna_id(prompt_2, constraints)


def test_trace_fingerprint_ignores_timestamp_noise() -> None:
    output_1 = {
        "response": {"type": "final", "content": "ok"},
        "plan": [{"step": 1, "intent": "Answer request"}],
        "tool_calls": [{"tool": "http_get"}],
        "trace": {
            "summary": "Executed at 2026-02-19T10:00:00Z with persona=general",
            "signals": {"uncertainty": "low", "assumptions": ["using docs v2"]},
        },
    }
    output_2 = {
        "response": {"type": "final", "content": "different text"},
        "plan": [{"step": 1, "intent": "Answer request"}],
        "tool_calls": [{"tool": "http_get"}],
        "trace": {
            "summary": "Executed at 2027-03-01T11:22:33Z with persona=general",
            "signals": {"uncertainty": "low", "assumptions": ["using docs v9"]},
        },
    }
    assert trace_fingerprint(output_1) == trace_fingerprint(output_2)
    assert behavior_fingerprint(output_1) == behavior_fingerprint(output_2)


def test_trace_fingerprint_changes_for_verbose_vs_concise_behavior() -> None:
    concise = {
        "response": {"type": "final", "content": "Hello."},
        "plan": [{"step": 1, "intent": "Answer request"}],
        "tool_calls": [],
        "trace": {"summary": "Executed at 2026-02-19T10:00:00Z", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    verbose = {
        "response": {"type": "final", "content": "Step 1: analyze\nStep 2: answer\nHello."},
        "plan": [{"step": 1, "intent": "Analyze"}, {"step": 2, "intent": "Answer"}],
        "tool_calls": [],
        "trace": {"summary": "Executed at 2027-03-01T11:22:33Z", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    assert trace_fingerprint(concise) != trace_fingerprint(verbose)
    assert behavior_fingerprint(concise) != behavior_fingerprint(verbose)


def test_run_trace_fp_can_differ_from_behavior_fp_on_retry_context() -> None:
    output = {
        "response": {"type": "final", "content": "Hello."},
        "plan": [{"step": 1, "intent": "Answer request"}],
        "tool_calls": [],
        "trace": {"summary": "runtime", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    assert trace_fingerprint(output, retried=False) != trace_fingerprint(output, retried=True)
    assert behavior_fingerprint(output).startswith("fp_")


def test_tool_sequence_fingerprint_stable_for_same_behavior() -> None:
    seq_1 = [
        {"tool_call_id": "tc1", "tool": "http_get", "allowed": True, "started_at": "t1"},
        {"tool_call_id": "tc2", "tool": "fs_read", "allowed": False, "blocked_reason": "forbidden_tool"},
    ]
    seq_2 = [
        {"tool_call_id": "another", "tool": "http_get", "allowed": True, "ended_at": "t9"},
        {"tool_call_id": "new", "tool": "fs_read", "allowed": False, "blocked_reason": "forbidden_tool"},
    ]
    assert tool_sequence_fingerprint(seq_1) == tool_sequence_fingerprint(seq_2)


def test_behavior_fingerprint_distinguishes_greeting_from_refusal() -> None:
    hello = {
        "response": {"type": "final", "content": "Hello."},
        "plan": [{"step": 1, "intent": "Answer request"}],
        "tool_calls": [],
        "trace": {"summary": "runtime", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    refusal = {
        "response": {"type": "final", "content": "I don't have an answer to that."},
        "plan": [{"step": 1, "intent": "Answer request"}],
        "tool_calls": [],
        "trace": {"summary": "runtime", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    assert behavior_fingerprint(hello) != behavior_fingerprint(refusal)


def test_behavior_fingerprint_distinguishes_user_specific_vs_generic_question() -> None:
    user_specific = {
        "response": {"type": "final", "content": "What is your favorite hobby?"},
        "plan": [{"step": 1, "intent": "Ask user"}],
        "tool_calls": [],
        "trace": {"summary": "runtime", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    generic = {
        "response": {"type": "final", "content": "What is the airspeed velocity of an unladen swallow?"},
        "plan": [{"step": 1, "intent": "Ask question"}],
        "tool_calls": [],
        "trace": {"summary": "runtime", "signals": {"uncertainty": "low", "assumptions": []}},
    }
    assert behavior_fingerprint(user_specific) != behavior_fingerprint(generic)
