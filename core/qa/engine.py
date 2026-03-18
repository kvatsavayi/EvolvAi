"""QA Engine: orchestrates running test suites against models and collecting results."""
from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.qa.models import (
    ComparisonReport,
    MUTConfig,
    ModelTestReport,
    TestCase,
    TestCaseResult,
    TestSuite,
)
from core.qa.validators import (
    build_llm_judge_prompt,
    parse_llm_judge_response,
    run_validators,
)
from core.qa.hybrid_judge import HybridJudge, HybridJudgeConfig, run_hybrid_evaluation


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QAEngine:
    """Runs QA test suites against one or more models.

    Supports:
    - Testing a single model
    - Comparing multiple models on the same suite
    - Running with/without LLM-as-Judge
    - Hybrid evaluation (normalizer + rules + LLM judge)
    - Regression testing against baseline results
    """

    def __init__(
        self,
        *,
        use_llm_judge: bool = False,
        judge_model: Optional[str] = None,
        consistency_runs: int = 1,
        use_hybrid_judge: bool = False,
        enable_normalizer: bool = True,
        hybrid_config: Optional[HybridJudgeConfig] = None,
    ) -> None:
        self.use_llm_judge = use_llm_judge
        self.judge_model = judge_model or "CLAUDE_V3_5_SONNET"
        self.consistency_runs = max(1, consistency_runs)
        self._client: Any = None

        # Hybrid judge configuration
        self.use_hybrid_judge = use_hybrid_judge
        if hybrid_config:
            self._hybrid_config = hybrid_config
        else:
            self._hybrid_config = HybridJudgeConfig(
                enable_normalizer=enable_normalizer,
                enable_llm_judge=use_llm_judge or use_hybrid_judge,
                judge_model=self.judge_model,
                normalizer_model=self.judge_model,
            )
        self._hybrid_judge: Optional[HybridJudge] = None

    @property
    def hybrid_judge(self) -> HybridJudge:
        if self._hybrid_judge is None:
            self._hybrid_judge = HybridJudge(self._hybrid_config)
        return self._hybrid_judge

    # ------------------------------------------------------------------
    # LLM client
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import abacusai
                self._client = abacusai.ApiClient()
            except Exception:
                self._client = None
        return self._client

    def _call_model(self, mut: MUTConfig, prompt: str) -> Dict[str, Any]:
        """Call a model and return the response with metadata."""
        start = time.time()

        if mut.provider == "mock":
            # Mock backend for testing
            elapsed = int((time.time() - start) * 1000) + 50
            return {
                "response": f"[Mock {mut.model_id}] Response to: {prompt[:80]}...",
                "latency_ms": elapsed,
                "token_counts": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 50, "total_tokens": len(prompt) // 4 + 50},
                "error": None,
            }

        client = self._get_client()
        if client is None:
            return {
                "response": f"[{mut.model_id}] ERROR: Abacus.AI client unavailable",
                "latency_ms": int((time.time() - start) * 1000),
                "token_counts": {},
                "error": "client_unavailable",
            }

        try:
            kwargs: Dict[str, Any] = {
                "prompt": prompt,
                "llm_name": mut.model_id,
                "max_tokens": mut.max_tokens,
                "temperature": mut.temperature,
                "system_message": mut.system_message,
            }
            if mut.top_p is not None:
                kwargs["top_p"] = mut.top_p

            resp = client.evaluate_prompt(**kwargs)
            elapsed_ms = int((time.time() - start) * 1000)

            return {
                "response": str(resp.content or "").strip(),
                "latency_ms": elapsed_ms,
                "token_counts": {
                    "prompt_tokens": int(resp.input_tokens or 0),
                    "completion_tokens": int(resp.output_tokens or 0),
                    "total_tokens": int(resp.input_tokens or 0) + int(resp.output_tokens or 0),
                },
                "error": None,
            }
        except Exception as exc:
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                "response": f"[{mut.model_id}] ERROR: {type(exc).__name__}: {exc}",
                "latency_ms": elapsed_ms,
                "token_counts": {},
                "error": f"{type(exc).__name__}: {exc}",
            }

    # ------------------------------------------------------------------
    # LLM-as-Judge
    # ------------------------------------------------------------------

    def _run_llm_judge(self, test_case: TestCase, response: str) -> Optional[Dict[str, Any]]:
        """Use a judge model to evaluate the response."""
        if not self.use_llm_judge:
            return None

        judge_prompt = build_llm_judge_prompt(test_case, response)
        judge_mut = MUTConfig(
            model_id=self.judge_model,
            provider="abacusai",
            temperature=0.1,
            max_tokens=512,
            system_message="You are an expert LLM output evaluator. Always respond in valid JSON.",
        )

        result = self._call_model(judge_mut, judge_prompt)
        if result["error"]:
            return None

        return parse_llm_judge_response(result["response"])

    # ------------------------------------------------------------------
    # Run test case
    # ------------------------------------------------------------------

    def run_test_case(
        self,
        mut: MUTConfig,
        test_case: TestCase,
        *,
        baseline_result: Optional[TestCaseResult] = None,
    ) -> TestCaseResult:
        """Run a single test case against a model."""
        # Primary run
        result = self._call_model(mut, test_case.prompt)

        # Consistency runs
        additional_responses: List[str] = []
        if self.consistency_runs > 1:
            for _ in range(self.consistency_runs - 1):
                extra = self._call_model(mut, test_case.prompt)
                if not extra["error"]:
                    additional_responses.append(extra["response"])

        # Choose evaluation path: hybrid judge or legacy validators
        if self.use_hybrid_judge:
            validation = run_hybrid_evaluation(
                test_case,
                result["response"],
                additional_responses=additional_responses if additional_responses else None,
                baseline_result=baseline_result,
                config=self._hybrid_config,
            )
        else:
            # Legacy path: optional LLM-as-Judge + rule validators
            llm_judge_scores = self._run_llm_judge(test_case, result["response"])
            validation = run_validators(
                test_case,
                result["response"],
                additional_responses=additional_responses if additional_responses else None,
                baseline_result=baseline_result,
                llm_judge_scores=llm_judge_scores,
            )

        return TestCaseResult(
            test_id=test_case.test_id,
            model_id=mut.model_id,
            prompt=test_case.prompt,
            response=result["response"],
            latency_ms=result["latency_ms"],
            token_counts=result["token_counts"],
            passed=validation["passed"],
            scores=validation["scores"],
            failures=validation["failures"],
            validator_details={v["validator"]: v for v in validation["validations"]},
            category=test_case.category,
            tags=test_case.tags,
            error=result["error"],
        )

    # ------------------------------------------------------------------
    # Run test suite
    # ------------------------------------------------------------------

    def run_test_suite(
        self,
        mut: MUTConfig,
        suite: TestSuite,
        *,
        baseline_results: Optional[Dict[str, TestCaseResult]] = None,
        progress_callback: Optional[Any] = None,
    ) -> ModelTestReport:
        """Run a full test suite against a model."""
        report = ModelTestReport(
            model_id=mut.model_id,
            suite_id=suite.suite_id,
        )

        for idx, test_case in enumerate(suite.test_cases):
            baseline = None
            if baseline_results and test_case.test_id in baseline_results:
                baseline = baseline_results[test_case.test_id]

            try:
                result = self.run_test_case(mut, test_case, baseline_result=baseline)
            except Exception as exc:
                result = TestCaseResult(
                    test_id=test_case.test_id,
                    model_id=mut.model_id,
                    prompt=test_case.prompt,
                    response=f"ERROR: {exc}",
                    passed=False,
                    failures=[f"execution_error:{type(exc).__name__}"],
                    error=str(exc),
                    category=test_case.category,
                )

            report.results.append(result)

            if progress_callback:
                progress_callback(idx + 1, len(suite.test_cases), result)

        report.compute_aggregates()
        return report

    # ------------------------------------------------------------------
    # Compare models
    # ------------------------------------------------------------------

    def compare_models(
        self,
        models: List[MUTConfig],
        suite: TestSuite,
        *,
        baseline_results: Optional[Dict[str, TestCaseResult]] = None,
        progress_callback: Optional[Any] = None,
    ) -> ComparisonReport:
        """Run the same test suite against multiple models and compare."""
        comparison = ComparisonReport(suite_id=suite.suite_id)

        for mut in models:
            report = self.run_test_suite(
                mut,
                suite,
                baseline_results=baseline_results,
                progress_callback=progress_callback,
            )
            comparison.model_reports[mut.model_id] = report

        return comparison

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def report_to_dict(report: ModelTestReport) -> Dict[str, Any]:
        """Serialize a report to a dict."""
        return {
            "report_id": report.report_id,
            "model_id": report.model_id,
            "suite_id": report.suite_id,
            "created_at": report.created_at,
            "total_tests": report.total_tests,
            "passed_tests": report.passed_tests,
            "failed_tests": report.failed_tests,
            "pass_rate": report.pass_rate,
            "avg_latency_ms": report.avg_latency_ms,
            "category_scores": report.category_scores,
            "attractors": report.attractors,
            "results": [
                {
                    "result_id": r.result_id,
                    "test_id": r.test_id,
                    "model_id": r.model_id,
                    "prompt": r.prompt[:200],
                    "response": r.response[:500],
                    "latency_ms": r.latency_ms,
                    "passed": r.passed,
                    "scores": r.scores,
                    "failures": r.failures,
                    "category": r.category,
                    "error": r.error,
                }
                for r in report.results
            ],
        }
