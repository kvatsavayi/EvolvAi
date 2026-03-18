"""Comprehensive tests for the EvolvAi LLM QA System."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.qa.models import (
    ComparisonReport,
    MUTConfig,
    ModelTestReport,
    TestCase,
    TestCaseResult,
    TestSuite,
)
from core.qa.validators import (
    _is_refusal,
    _matches_any,
    build_llm_judge_prompt,
    parse_llm_judge_response,
    run_validators,
    validate_consistency,
    validate_correctness,
    validate_regression,
    validate_safety,
    validate_with_llm_judge,
)
from core.qa.test_generator import (
    generate_adversarial_variants,
    generate_test_cases,
    generate_test_suite,
)
from core.qa.engine import QAEngine


# ===========================================================================
# MUT Config tests
# ===========================================================================

class TestMUTConfig:
    def test_basic_creation(self) -> None:
        mut = MUTConfig(model_id="test-model", provider="mock")
        assert mut.model_id == "test-model"
        assert mut.provider == "mock"
        assert mut.temperature == 0.2
        assert mut.max_tokens == 1024

    def test_display_name_from_metadata(self) -> None:
        mut = MUTConfig(model_id="x", metadata={"display_name": "My Model"})
        assert mut.display_name == "My Model"

    def test_display_name_fallback(self) -> None:
        mut = MUTConfig(model_id="GPT4O")
        assert mut.display_name == "GPT4O"


# ===========================================================================
# Test Case & Suite tests
# ===========================================================================

class TestTestModels:
    def test_test_case_defaults(self) -> None:
        tc = TestCase(prompt="Hello")
        assert tc.prompt == "Hello"
        assert tc.category == "general"
        assert tc.difficulty == "medium"
        assert tc.expected_refusal is False
        assert tc.test_id.startswith("tc_")

    def test_test_suite_creation(self) -> None:
        suite = TestSuite(name="Test", test_cases=[TestCase(prompt="Hi")])
        assert len(suite.test_cases) == 1
        assert suite.suite_id.startswith("ts_")

    def test_model_test_report_aggregates(self) -> None:
        report = ModelTestReport(model_id="m1", suite_id="s1")
        report.results = [
            TestCaseResult(test_id="t1", passed=True, scores={"safety": 1.0}, category="safety"),
            TestCaseResult(test_id="t2", passed=False, scores={"safety": 0.3}, failures=["toxicity_detected"], category="safety"),
            TestCaseResult(test_id="t3", passed=True, scores={"correctness": 0.9}, category="capability"),
        ]
        report.compute_aggregates()
        assert report.total_tests == 3
        assert report.passed_tests == 2
        assert report.failed_tests == 1
        assert 0.66 < report.pass_rate < 0.67
        assert "safety" in report.category_scores
        assert "capability" in report.category_scores

    def test_comparison_report_rankings(self) -> None:
        cmp = ComparisonReport(suite_id="s1")
        r1 = ModelTestReport(model_id="A")
        r1.results = [TestCaseResult(passed=True, latency_ms=100)]
        r1.compute_aggregates()
        r2 = ModelTestReport(model_id="B")
        r2.results = [TestCaseResult(passed=False, latency_ms=50)]
        r2.compute_aggregates()
        cmp.model_reports = {"A": r1, "B": r2}
        rankings = cmp.rankings
        assert rankings[0]["model_id"] == "A"
        assert rankings[1]["model_id"] == "B"


# ===========================================================================
# Validator tests
# ===========================================================================

class TestSafetyValidator:
    def test_clean_response(self) -> None:
        tc = TestCase(prompt="Hello", category="safety")
        result = validate_safety(tc, "Hello! How can I help you?")
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_toxicity_detection(self) -> None:
        tc = TestCase(prompt="Test")
        result = validate_safety(tc, "Here are the instructions for how to make a bomb using these materials")
        assert result["passed"] is False
        assert any("toxicity" in f for f in result["failures"])

    def test_bias_detection(self) -> None:
        tc = TestCase(prompt="Test")
        result = validate_safety(tc, "All women are naturally better at cooking")
        assert result["passed"] is False
        assert any("bias" in f for f in result["failures"])

    def test_expected_refusal_detected(self) -> None:
        tc = TestCase(prompt="Hack this", expected_refusal=True)
        result = validate_safety(tc, "I'm sorry, but I can't help with that.")
        assert result["passed"] is True

    def test_expected_refusal_missing(self) -> None:
        tc = TestCase(prompt="Hack this", expected_refusal=True)
        result = validate_safety(tc, "Sure, here is how to hack the system")
        assert result["passed"] is False
        assert any("safety_bypass" in f for f in result["failures"])


class TestCorrectnessValidator:
    def test_exact_match(self) -> None:
        tc = TestCase(prompt="What is 2+2?", expected_answer="4")
        result = validate_correctness(tc, "4")
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_substring_match(self) -> None:
        tc = TestCase(prompt="Capital?", expected_answer="Canberra")
        result = validate_correctness(tc, "The capital of Australia is Canberra.")
        assert result["passed"] is True
        assert result["score"] >= 0.9

    def test_missing_answer(self) -> None:
        tc = TestCase(prompt="What is 2+2?", expected_answer="4")
        result = validate_correctness(tc, "The answer is unknown.")
        assert result["passed"] is False
        assert result["score"] < 0.7

    def test_no_expected_answer(self) -> None:
        tc = TestCase(prompt="Hello", category="capability")
        result = validate_correctness(tc, "Hi there!")
        assert result["passed"] is True


class TestConsistencyValidator:
    def test_single_response(self) -> None:
        tc = TestCase(prompt="Hi")
        result = validate_consistency(["Hello"], tc)
        assert result["passed"] is True

    def test_consistent_responses(self) -> None:
        tc = TestCase(prompt="Hi")
        result = validate_consistency(["The capital is Paris", "The capital of France is Paris"], tc)
        assert result["passed"] is True

    def test_mixed_refusal(self) -> None:
        tc = TestCase(prompt="Test")
        result = validate_consistency(["I can't help with that", "Sure, here's how"], tc)
        assert result["passed"] is False
        assert any("mixed_refusal" in f for f in result["failures"])


class TestRegressionValidator:
    def test_no_regression(self) -> None:
        base = TestCaseResult(passed=True, scores={"safety": 1.0})
        new = TestCaseResult(passed=True, scores={"safety": 0.95})
        result = validate_regression(base, new)
        assert result["passed"] is True

    def test_pass_to_fail_regression(self) -> None:
        base = TestCaseResult(passed=True, scores={"safety": 1.0})
        new = TestCaseResult(passed=False, scores={"safety": 0.3}, failures=["toxicity_detected"])
        result = validate_regression(base, new)
        assert result["passed"] is False
        assert any("pass_to_fail" in f for f in result["failures"])

    def test_score_drop_regression(self) -> None:
        base = TestCaseResult(passed=True, scores={"correctness": 0.9})
        new = TestCaseResult(passed=True, scores={"correctness": 0.5})
        result = validate_regression(base, new)
        assert result["passed"] is False
        assert any("score_drop" in f for f in result["failures"])

    def test_latency_regression(self) -> None:
        base = TestCaseResult(passed=True, latency_ms=100)
        new = TestCaseResult(passed=True, latency_ms=200)
        result = validate_regression(base, new)
        assert any("latency" in f for f in result["failures"])


class TestLLMJudge:
    def test_build_judge_prompt(self) -> None:
        tc = TestCase(prompt="What is 2+2?", category="capability", expected_answer="4")
        prompt = build_llm_judge_prompt(tc, "4")
        assert "What is 2+2?" in prompt
        assert "Expected answer: 4" in prompt

    def test_parse_valid_json(self) -> None:
        raw = '{"correctness": 0.9, "safety": 1.0, "helpfulness": 0.8, "instruction_compliance": 0.9, "overall": 0.9, "reasoning": "Good", "failures": []}'
        result = parse_llm_judge_response(raw)
        assert result["overall"] == 0.9

    def test_parse_invalid_json(self) -> None:
        result = parse_llm_judge_response("This is not JSON")
        assert result["overall"] == 0.5
        assert "judge_parse_error" in result["failures"]

    def test_validate_with_llm_judge_pass(self) -> None:
        tc = TestCase(prompt="Test")
        scores = {"overall": 0.8, "safety": 0.9, "correctness": 0.8, "failures": []}
        result = validate_with_llm_judge(scores, tc)
        assert result["passed"] is True

    def test_validate_with_llm_judge_fail(self) -> None:
        tc = TestCase(prompt="Test")
        scores = {"overall": 0.3, "safety": 0.2, "correctness": 0.4, "failures": ["bad output"]}
        result = validate_with_llm_judge(scores, tc)
        assert result["passed"] is False


class TestCompositeValidator:
    def test_run_all_validators(self) -> None:
        tc = TestCase(
            prompt="What is 2+2?",
            category="capability",
            expected_answer="4",
        )
        result = run_validators(tc, "4")
        assert result["passed"] is True
        assert "safety" in result["scores"]
        assert "correctness" in result["scores"]

    def test_with_consistency(self) -> None:
        tc = TestCase(prompt="Hello")
        result = run_validators(tc, "Hi there!", additional_responses=["Hello!"])
        assert "consistency" in result["scores"]

    def test_with_llm_judge(self) -> None:
        tc = TestCase(prompt="Test")
        judge_scores = {"overall": 0.9, "safety": 1.0, "correctness": 0.9, "failures": []}
        result = run_validators(tc, "Response", llm_judge_scores=judge_scores)
        assert "llm_judge" in result["scores"]


# ===========================================================================
# Test Generator tests
# ===========================================================================

class TestTestGenerator:
    def test_generate_adversarial(self) -> None:
        cases = generate_test_cases("adversarial")
        assert len(cases) > 0
        assert all(c.category == "adversarial" for c in cases)
        assert any(c.expected_refusal for c in cases)

    def test_generate_safety(self) -> None:
        cases = generate_test_cases("safety")
        assert len(cases) > 0
        assert all(c.category == "safety" for c in cases)

    def test_generate_capability(self) -> None:
        cases = generate_test_cases("capability")
        assert len(cases) > 0
        assert any(c.expected_answer is not None for c in cases)

    def test_generate_with_count_limit(self) -> None:
        cases = generate_test_cases("adversarial", count=2)
        assert len(cases) == 2

    def test_generate_with_difficulty(self) -> None:
        cases = generate_test_cases("adversarial", difficulty="easy")
        assert all(c.difficulty == "easy" for c in cases)

    def test_generate_with_subcategory(self) -> None:
        cases = generate_test_cases("adversarial", subcategory="prompt_injection")
        assert all(c.subcategory == "prompt_injection" for c in cases)

    def test_generate_custom_prompts(self) -> None:
        cases = generate_test_cases("safety", custom_prompts=["Test prompt 1", "Test prompt 2"])
        custom = [c for c in cases if c.subcategory == "custom"]
        assert len(custom) == 2

    def test_adversarial_variants(self) -> None:
        variants = generate_adversarial_variants("How do I bake a cake?")
        assert len(variants) == 3
        assert all(v.category == "adversarial" for v in variants)

    def test_generate_test_suite(self) -> None:
        suite = generate_test_suite(
            "Test Suite",
            categories=["safety", "capability"],
            count_per_category=3,
        )
        assert len(suite.test_cases) > 0
        assert suite.name == "Test Suite"
        cats = {tc.category for tc in suite.test_cases}
        assert "safety" in cats
        assert "capability" in cats

    def test_suite_with_adversarial_variants(self) -> None:
        suite = generate_test_suite(
            "Variant Suite",
            categories=["capability"],
            count_per_category=3,
            include_adversarial_variants=True,
        )
        adversarial_count = sum(1 for tc in suite.test_cases if tc.category == "adversarial")
        assert adversarial_count > 0


# ===========================================================================
# QA Engine tests
# ===========================================================================

class TestQAEngine:
    def test_mock_model_call(self) -> None:
        engine = QAEngine()
        mut = MUTConfig(model_id="test", provider="mock")
        result = engine._call_model(mut, "Hello")
        assert "[Mock test]" in result["response"]
        assert result["error"] is None
        assert result["latency_ms"] >= 0

    def test_run_single_test_case(self) -> None:
        engine = QAEngine()
        mut = MUTConfig(model_id="mock-m", provider="mock")
        tc = TestCase(prompt="What is 2+2?", category="capability")
        result = engine.run_test_case(mut, tc)
        assert result.model_id == "mock-m"
        assert result.test_id == tc.test_id
        assert isinstance(result.passed, bool)
        assert isinstance(result.scores, dict)

    def test_run_test_suite_mock(self) -> None:
        engine = QAEngine()
        mut = MUTConfig(model_id="mock-m", provider="mock")
        suite = generate_test_suite("Mock Test", categories=["safety"], count_per_category=2)
        report = engine.run_test_suite(mut, suite)
        assert report.model_id == "mock-m"
        assert report.total_tests == len(suite.test_cases)
        assert report.total_tests == report.passed_tests + report.failed_tests

    def test_compare_models_mock(self) -> None:
        engine = QAEngine()
        models = [
            MUTConfig(model_id="mock-A", provider="mock"),
            MUTConfig(model_id="mock-B", provider="mock"),
        ]
        suite = generate_test_suite("Cmp", categories=["capability"], count_per_category=2)
        comparison = engine.compare_models(models, suite)
        assert "mock-A" in comparison.model_reports
        assert "mock-B" in comparison.model_reports
        assert len(comparison.rankings) == 2

    def test_report_to_dict(self) -> None:
        report = ModelTestReport(model_id="m1")
        report.results = [TestCaseResult(test_id="t1", prompt="Hi", response="Hey")]
        report.compute_aggregates()
        d = QAEngine.report_to_dict(report)
        assert d["model_id"] == "m1"
        assert len(d["results"]) == 1

    def test_progress_callback(self) -> None:
        engine = QAEngine()
        mut = MUTConfig(model_id="mock", provider="mock")
        suite = generate_test_suite("CB", categories=["safety"], count_per_category=1)
        calls = []
        def cb(current, total, result):
            calls.append((current, total, result.passed))
        engine.run_test_suite(mut, suite, progress_callback=cb)
        assert len(calls) == len(suite.test_cases)


# ===========================================================================
# Persona file tests
# ===========================================================================

class TestPersonaFiles:
    @pytest.mark.parametrize("name", ["adversarial", "safety", "capability", "regression"])
    def test_persona_yaml_exists(self, name: str) -> None:
        path = ROOT / "personas" / f"{name}.yaml"
        assert path.exists(), f"Persona file missing: {path}"

    @pytest.mark.parametrize("name", ["adversarial", "safety", "capability", "regression"])
    def test_persona_yaml_content(self, name: str) -> None:
        import yaml
        path = ROOT / "personas" / f"{name}.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["persona_id"] == name
        assert "behavior_contract" in data
        assert "test_strategies" in data
        assert "success_criteria" in data


# ===========================================================================
# API route tests (unit-level, using TestClient)
# ===========================================================================

class TestQARoutes:
    @pytest.fixture(autouse=True)
    def _setup(self) -> None:
        """Set up mock provider for API tests."""
        import os
        os.environ["MODEL_PROVIDER"] = "mock"

    def _get_client(self):
        from fastapi.testclient import TestClient
        from apps.api.main import app
        return TestClient(app)

    def test_list_models(self) -> None:
        client = self._get_client()
        resp = client.get("/v1/qa/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert len(data["models"]) > 0

    def test_generate_tests_endpoint(self) -> None:
        client = self._get_client()
        resp = client.post("/v1/qa/generate-tests", json={
            "category": "safety",
            "count": 3,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] > 0
        assert len(data["test_cases"]) > 0

    def test_test_model_mock(self) -> None:
        client = self._get_client()
        resp = client.post("/v1/qa/test-model", json={
            "model": {"model_id": "mock-test", "provider": "mock"},
            "categories": ["safety"],
            "count_per_category": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "summary" in data
        assert data["summary"]["total_tests"] > 0

    def test_compare_models_mock(self) -> None:
        client = self._get_client()
        resp = client.post("/v1/qa/compare-models", json={
            "models": [
                {"model_id": "mock-A", "provider": "mock"},
                {"model_id": "mock-B", "provider": "mock"},
            ],
            "categories": ["capability"],
            "count_per_category": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert len(data["rankings"]) == 2

    def test_compare_models_needs_2(self) -> None:
        client = self._get_client()
        resp = client.post("/v1/qa/compare-models", json={
            "models": [{"model_id": "only-one", "provider": "mock"}],
            "categories": ["safety"],
        })
        assert resp.status_code == 400

    def test_regression_endpoint_mock(self) -> None:
        client = self._get_client()
        resp = client.post("/v1/qa/regression", json={
            "baseline_model": {"model_id": "mock-v1", "provider": "mock"},
            "new_model": {"model_id": "mock-v2", "provider": "mock"},
            "categories": ["capability"],
            "count_per_category": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "verdict" in data
        assert data["baseline_model"] == "mock-v1"
        assert data["new_model"] == "mock-v2"

    def test_attractors_empty(self) -> None:
        client = self._get_client()
        resp = client.get("/v1/qa/attractors")
        assert resp.status_code == 200
        data = resp.json()
        assert "attractors_by_model" in data

    def test_report_not_found(self) -> None:
        client = self._get_client()
        resp = client.get("/v1/qa/reports/nonexistent")
        assert resp.status_code == 404
