from core.judge.judge import Judge


def _base_output(content: str) -> dict:
    return {
        "executor_output_id": "xo_1",
        "run_id": "run_1",
        "response": {"type": "final", "content": content},
        "trace": {"summary": "s"},
        "tool_calls": [],
    }


def test_judge_flags_one_line_instruction_violation() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_1",
        snapshot={"snapshot_id": "snap_1", "request": {"user_input": "Say hello in one line"}},
        executor_output=_base_output("Hello\nWorld"),
        tool_results=[],
    )
    assert result["pass"] is False
    assert any(f["code"] == "INSTRUCTION_VIOLATION_ONE_LINE" for f in result["failures"])


def test_judge_flags_tool_hallucination_without_tool_calls() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_2",
        snapshot={"snapshot_id": "snap_2", "request": {"user_input": "say hello"}},
        executor_output=_base_output("Using http_get tool, hello."),
        tool_results=[],
    )
    assert result["pass"] is False
    assert any(f["code"] == "TOOL_HALLUCINATION" for f in result["failures"])


def test_judge_flags_schema_echo() -> None:
    judge = Judge()
    content = "```json\n{\"type\":\"final\",\"structured\":{}}\n```"
    result = judge.evaluate(
        run_id="run_3",
        snapshot={"snapshot_id": "snap_3", "request": {"user_input": "say hello"}},
        executor_output=_base_output(content),
        tool_results=[],
    )
    assert result["pass"] is False
    assert any(f["code"] == "SCHEMA_ECHO" for f in result["failures"])


def test_judge_allows_schemaish_content_when_workflow_has_tool_calls() -> None:
    judge = Judge()
    output = _base_output('{"response":{"type":"final","content":"done"}}')
    output["tool_calls"] = [
        {"tool_call_id": "tc_1", "tool": "write_file", "args": {"path": "data/workspaces/ws_1/app/main.py", "content": "x"}}
    ]
    result = judge.evaluate(
        run_id="run_3b",
        snapshot={
            "snapshot_id": "snap_3b",
            "request": {"user_input": "build a simple CRUD application"},
            "context": {"state": {"workflow_goal": "generic_build_app", "persona_id": "implementation"}},
        },
        executor_output=output,
        tool_results=[],
    )
    assert not any(f["code"] == "SCHEMA_ECHO" for f in result["failures"])


def test_efficiency_penalizes_planning_fluff() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_4",
        snapshot={"snapshot_id": "snap_4", "request": {"user_input": "say hello in one line"}},
        executor_output=_base_output("Step 1: think\nStep 2: answer"),
        tool_results=[],
    )
    assert result["scores"]["efficiency"] < 1.0


def test_judge_flags_unnecessary_refusal_for_ask_me_prompt() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_5",
        snapshot={"snapshot_id": "snap_5", "request": {"user_input": "ask me something you genuinely dont have an answer for"}},
        executor_output=_base_output("I don't have an answer to that."),
        tool_results=[],
    )
    assert result["pass"] is False
    codes = {f["code"] for f in result["failures"]}
    assert "INTENT_MISMATCH" in codes
    assert "UNNECESSARY_REFUSAL" in codes


def test_judge_flags_non_user_specific_question_for_ask_me_prompt() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_6",
        snapshot={"snapshot_id": "snap_6", "request": {"user_input": "ask me something you genuinely dont have an answer for"}},
        executor_output=_base_output("What is the meaning of life?"),
        tool_results=[],
    )
    assert result["pass"] is False
    assert any(f["code"] == "QUESTION_NOT_USER_SPECIFIC" for f in result["failures"])


def test_judge_requires_workflow_tools_for_implementation_persona() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_7",
        snapshot={
            "snapshot_id": "snap_7",
            "request": {"user_input": "build a weather station app"},
            "context": {"state": {"workflow_goal": "weather_station_app", "persona_id": "implementation"}},
        },
        executor_output=_base_output("Implemented the weather station app."),
        tool_results=[],
    )
    assert result["pass"] is False
    assert any(f["code"] == "WORKFLOW_TOOL_MISSING" for f in result["failures"])


def test_judge_rejects_qa_source_text_assertions(tmp_path) -> None:
    judge = Judge()
    test_root = tmp_path / "tests"
    test_root.mkdir(parents=True)
    (test_root / "test_weather.py").write_text(
        "from pathlib import Path\n\n"
        "def test_weather_route_exists() -> None:\n"
        "    src = Path('app/main.py').read_text(encoding='utf-8')\n"
        "    assert \"@app.get('/weather')\" in src\n",
        encoding="utf-8",
    )
    result = judge.evaluate(
        run_id="run_8",
        snapshot={
            "snapshot_id": "snap_8",
            "request": {"user_input": "build a weather station app"},
            "context": {"state": {"workflow_goal": "weather_station_app", "persona_id": "qa_test"}},
        },
        executor_output=_base_output("QA completed."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "workspace_run",
                "allowed": True,
                "result": {
                    "cmd": f"python3 -m pytest {test_root} -q",
                    "exit_code": 0,
                    "stdout": "1 passed in 0.01s",
                },
            }
        ],
    )
    assert result["pass"] is False
    assert any(f["code"] == "QA_QUALITY_FAILED" for f in result["failures"])


def test_judge_accepts_behavioral_qa_tests(tmp_path) -> None:
    judge = Judge()
    test_root = tmp_path / "tests"
    test_root.mkdir(parents=True)
    (test_root / "test_weather.py").write_text(
        "from fastapi.testclient import TestClient\n\n"
        "def test_weather_endpoint_behavior(app):\n"
        "    client = TestClient(app)\n"
        "    r = client.get('/weather', params={'city': 'Boston'})\n"
        "    assert r.status_code == 200\n",
        encoding="utf-8",
    )
    result = judge.evaluate(
        run_id="run_9",
        snapshot={
            "snapshot_id": "snap_9",
            "request": {"user_input": "build a weather station app"},
            "context": {"state": {"workflow_goal": "weather_station_app", "persona_id": "qa_test"}},
        },
        executor_output=_base_output("QA completed."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "workspace_run",
                "allowed": True,
                "result": {
                    "cmd": f"python3 -m pytest {test_root} -q",
                    "exit_code": 0,
                    "stdout": "1 passed in 0.01s",
                },
            }
        ],
    )
    assert result["pass"] is True


def test_judge_accepts_relative_tests_cmd_with_cwd(tmp_path) -> None:
    judge = Judge()
    test_root = tmp_path / "tests"
    test_root.mkdir(parents=True)
    (test_root / "test_main.py").write_text(
        "from fastapi.testclient import TestClient\n\n"
        "def test_health_behavior(app):\n"
        "    client = TestClient(app)\n"
        "    r = client.get('/health')\n"
        "    assert r.status_code == 200\n",
        encoding="utf-8",
    )
    result = judge.evaluate(
        run_id="run_9b",
        snapshot={
            "snapshot_id": "snap_9b",
            "request": {"user_input": "build a simple crud app"},
            "context": {"state": {"workflow_goal": "service_bootstrap_app", "persona_id": "qa_test"}},
        },
        executor_output=_base_output("QA completed."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "workspace_run",
                "allowed": True,
                "result": {
                    "cmd": "python3 -m pytest tests -q",
                    "cwd": str(tmp_path),
                    "exit_code": 0,
                    "stdout": "1 passed in 0.01s",
                },
            }
        ],
    )
    assert result["pass"] is True


def test_judge_requires_required_files_for_weather_implementation() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_10",
        snapshot={
            "snapshot_id": "snap_10",
            "request": {"user_input": "build a weather station app"},
            "context": {"state": {"workflow_goal": "weather_station_app", "persona_id": "implementation"}},
        },
        executor_output=_base_output("Implemented weather service."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "write_file",
                "allowed": True,
                "result": {"path": "/app/data/workspaces/ws_1/weather_station_app.txt", "bytes_written": 20},
            }
        ],
    )
    assert result["pass"] is False
    assert any(f["code"] == "WORKFLOW_FILE_MISSING" for f in result["failures"])


def test_judge_accepts_required_files_for_weather_implementation() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_11",
        snapshot={
            "snapshot_id": "snap_11",
            "request": {"user_input": "build a weather station app"},
            "context": {"state": {"workflow_goal": "weather_station_app", "persona_id": "implementation"}},
        },
        executor_output=_base_output("Implemented weather service."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "write_file",
                "allowed": True,
                "result": {"path": "/app/data/workspaces/ws_1/app/main.py", "bytes_written": 120},
            },
            {
                "tool_call_id": "tc2",
                "tool": "write_file",
                "allowed": True,
                "result": {"path": "/app/data/workspaces/ws_1/tests/test_weather.py", "bytes_written": 180},
            },
        ],
    )
    assert result["pass"] is True


def test_judge_requires_required_files_for_generic_build_app() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_12",
        snapshot={
            "snapshot_id": "snap_12",
            "request": {"user_input": "build a simple CRUD application"},
            "context": {"state": {"workflow_goal": "generic_build_app", "persona_id": "implementation"}},
        },
        executor_output=_base_output("Implemented app."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "write_file",
                "allowed": True,
                "result": {"path": "/app/data/workspaces/ws_1/README.md", "bytes_written": 42},
            }
        ],
    )
    assert result["pass"] is False
    assert any(f["code"] == "WORKFLOW_FILE_MISSING" for f in result["failures"])


def test_judge_requires_readme_for_service_bootstrap_app() -> None:
    judge = Judge()
    result = judge.evaluate(
        run_id="run_13",
        snapshot={
            "snapshot_id": "snap_13",
            "request": {"user_input": "build a service bootstrap app"},
            "context": {"state": {"workflow_goal": "service_bootstrap_app", "persona_id": "implementation"}},
        },
        executor_output=_base_output("Implemented app."),
        tool_results=[
            {
                "tool_call_id": "tc1",
                "tool": "write_file",
                "allowed": True,
                "result": {"path": "/app/data/workspaces/ws_1/app/main.py", "bytes_written": 200},
            },
            {
                "tool_call_id": "tc2",
                "tool": "write_file",
                "allowed": True,
                "result": {"path": "/app/data/workspaces/ws_1/tests/test_main.py", "bytes_written": 240},
            },
        ],
    )
    assert result["pass"] is False
    assert any(f["code"] == "WORKFLOW_FILE_MISSING" for f in result["failures"])
