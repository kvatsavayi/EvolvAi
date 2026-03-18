"""QA System API routes: test models, compare, discover attractors, run regressions."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppState, get_state
from core.qa.engine import QAEngine
from core.qa.models import (
    ComparisonReport,
    MUTConfig,
    ModelTestReport,
    TestCase,
    TestCaseResult,
    TestSuite,
)
from core.qa.test_generator import (
    generate_adversarial_variants,
    generate_test_cases,
    generate_test_suite,
)


qa_router = APIRouter(prefix="/v1/qa", tags=["qa"])


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class MUTConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    provider: str = "abacusai"
    temperature: float = 0.2
    max_tokens: int = 1024
    top_p: Optional[float] = None
    system_message: str = "You are a helpful AI assistant."
    display_name: Optional[str] = None


class TestModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: MUTConfigRequest
    categories: List[str] = Field(default_factory=lambda: ["adversarial", "safety", "capability"])
    count_per_category: int = 5
    difficulty: Optional[str] = None
    use_llm_judge: bool = False
    judge_model: Optional[str] = None
    consistency_runs: int = 1
    custom_prompts: Optional[List[str]] = None


class CompareModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: List[MUTConfigRequest]
    categories: List[str] = Field(default_factory=lambda: ["capability", "safety"])
    count_per_category: int = 3
    difficulty: Optional[str] = None
    use_llm_judge: bool = False
    judge_model: Optional[str] = None


class RegressionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_model: MUTConfigRequest
    new_model: MUTConfigRequest
    categories: List[str] = Field(default_factory=lambda: ["capability", "regression"])
    count_per_category: int = 5
    use_llm_judge: bool = False


class GenerateTestsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    count: Optional[int] = None
    difficulty: Optional[str] = None
    subcategory: Optional[str] = None
    custom_prompts: Optional[List[str]] = None
    include_adversarial_variants: bool = False


# ---------------------------------------------------------------------------
# Helper: convert request model to MUTConfig
# ---------------------------------------------------------------------------

def _to_mut(req: MUTConfigRequest) -> MUTConfig:
    return MUTConfig(
        model_id=req.model_id,
        provider=req.provider,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        top_p=req.top_p,
        system_message=req.system_message,
        metadata={"display_name": req.display_name or req.model_id},
    )


# ---------------------------------------------------------------------------
# In-memory report storage (for demo; production should use DB/artifact store)
# ---------------------------------------------------------------------------

_report_store: Dict[str, Dict[str, Any]] = {}
_comparison_store: Dict[str, Dict[str, Any]] = {}
_attractor_store: Dict[str, List[Dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@qa_router.post("/test-model")
def test_model(payload: TestModelRequest, state: AppState = Depends(get_state)) -> Dict[str, Any]:
    """Test a specific model with an auto-generated test suite."""
    mut = _to_mut(payload.model)

    suite = generate_test_suite(
        name=f"QA Test: {mut.model_id}",
        categories=payload.categories,
        count_per_category=payload.count_per_category,
        difficulty=payload.difficulty,  # type: ignore
    )

    # Add custom prompts if provided
    if payload.custom_prompts:
        for prompt in payload.custom_prompts:
            suite.test_cases.append(TestCase(
                prompt=prompt,
                category="custom",
                subcategory="user_provided",
                expected_behavior="User-defined test",
                tags=["custom"],
            ))

    engine = QAEngine(
        use_llm_judge=payload.use_llm_judge,
        judge_model=payload.judge_model,
        consistency_runs=payload.consistency_runs,
    )

    report = engine.run_test_suite(mut, suite)
    report_dict = engine.report_to_dict(report)

    # Store report and attractors
    _report_store[report.report_id] = report_dict
    if report.attractors:
        _attractor_store[mut.model_id] = report.attractors

    # Record lineage edge
    try:
        from core.pod.lineage import make_lineage_edge
        edge = make_lineage_edge(
            parent_type="test_suite",
            parent_id=suite.suite_id,
            child_type="qa_report",
            child_id=report.report_id,
            reason=f"qa_test_model:{mut.model_id}",
        )
        state.db.execute(
            "INSERT OR IGNORE INTO lineage_edges (edge_id, created_at, parent_type, parent_id, child_type, child_id, reason, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (edge.edge_id, edge.created_at, edge.parent_type, edge.parent_id, edge.child_type, edge.child_id, edge.reason, edge.run_id),
        )
    except Exception:
        pass  # lineage is best-effort

    return {
        "status": "completed",
        "report_id": report.report_id,
        "model_id": mut.model_id,
        "summary": {
            "total_tests": report.total_tests,
            "passed": report.passed_tests,
            "failed": report.failed_tests,
            "pass_rate": round(report.pass_rate, 4),
            "avg_latency_ms": round(report.avg_latency_ms, 2),
        },
        "category_scores": report.category_scores,
        "attractors": report.attractors,
        "report": report_dict,
    }


@qa_router.post("/compare-models")
def compare_models(payload: CompareModelsRequest, state: AppState = Depends(get_state)) -> Dict[str, Any]:
    """Compare multiple models on the same test suite."""
    if len(payload.models) < 2:
        raise HTTPException(status_code=400, detail="At least 2 models required for comparison")

    models = [_to_mut(m) for m in payload.models]

    suite = generate_test_suite(
        name=f"Comparison: {', '.join(m.model_id for m in models)}",
        categories=payload.categories,
        count_per_category=payload.count_per_category,
        difficulty=payload.difficulty,  # type: ignore
    )

    engine = QAEngine(
        use_llm_judge=payload.use_llm_judge,
        judge_model=payload.judge_model,
    )

    comparison = engine.compare_models(models, suite)

    result = comparison.to_dict()
    _comparison_store[comparison.comparison_id] = result

    return {
        "status": "completed",
        "comparison_id": comparison.comparison_id,
        "suite_id": suite.suite_id,
        "rankings": comparison.rankings,
        "model_summaries": {
            mid: {
                "pass_rate": rpt.pass_rate,
                "avg_latency_ms": round(rpt.avg_latency_ms, 2),
                "attractors": len(rpt.attractors),
            }
            for mid, rpt in comparison.model_reports.items()
        },
        "full_report": result,
    }


@qa_router.post("/regression")
def run_regression(payload: RegressionRequest, state: AppState = Depends(get_state)) -> Dict[str, Any]:
    """Compare model versions: run baseline, then new model, flag regressions."""
    baseline_mut = _to_mut(payload.baseline_model)
    new_mut = _to_mut(payload.new_model)

    suite = generate_test_suite(
        name=f"Regression: {baseline_mut.model_id} -> {new_mut.model_id}",
        categories=payload.categories,
        count_per_category=payload.count_per_category,
    )

    engine = QAEngine(use_llm_judge=payload.use_llm_judge)

    # Run baseline
    baseline_report = engine.run_test_suite(baseline_mut, suite)

    # Build baseline results map
    baseline_map: Dict[str, TestCaseResult] = {
        r.test_id: r for r in baseline_report.results
    }

    # Run new model with regression comparison
    new_report = engine.run_test_suite(new_mut, suite, baseline_results=baseline_map)

    # Find regressions
    regressions: List[Dict[str, Any]] = []
    improvements: List[Dict[str, Any]] = []
    for new_r in new_report.results:
        base_r = baseline_map.get(new_r.test_id)
        if base_r is None:
            continue
        if base_r.passed and not new_r.passed:
            regressions.append({
                "test_id": new_r.test_id,
                "prompt": new_r.prompt[:150],
                "type": "pass_to_fail",
                "baseline_scores": base_r.scores,
                "new_scores": new_r.scores,
                "new_failures": new_r.failures,
            })
        elif not base_r.passed and new_r.passed:
            improvements.append({
                "test_id": new_r.test_id,
                "prompt": new_r.prompt[:150],
                "type": "fail_to_pass",
            })

    return {
        "status": "completed",
        "baseline_model": baseline_mut.model_id,
        "new_model": new_mut.model_id,
        "baseline_summary": {
            "pass_rate": round(baseline_report.pass_rate, 4),
            "total": baseline_report.total_tests,
        },
        "new_summary": {
            "pass_rate": round(new_report.pass_rate, 4),
            "total": new_report.total_tests,
        },
        "regressions": regressions,
        "improvements": improvements,
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
        "verdict": "regression_detected" if regressions else "no_regression",
    }


@qa_router.get("/attractors")
def get_attractors() -> Dict[str, Any]:
    """View discovered failure patterns (attractors) across models."""
    return {
        "attractors_by_model": _attractor_store,
        "total_models_tested": len(_attractor_store),
        "total_attractors": sum(len(v) for v in _attractor_store.values()),
    }


@qa_router.get("/attractors/{model_id}")
def get_model_attractors(model_id: str) -> Dict[str, Any]:
    """View attractors for a specific model."""
    attractors = _attractor_store.get(model_id)
    if attractors is None:
        raise HTTPException(status_code=404, detail=f"No attractors found for model {model_id}")
    return {"model_id": model_id, "attractors": attractors}


@qa_router.get("/reports/{report_id}")
def get_report(report_id: str) -> Dict[str, Any]:
    """Retrieve a previously generated QA report."""
    report = _report_store.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")
    return report


@qa_router.get("/comparisons/{comparison_id}")
def get_comparison(comparison_id: str) -> Dict[str, Any]:
    """Retrieve a previously generated comparison report."""
    comparison = _comparison_store.get(comparison_id)
    if comparison is None:
        raise HTTPException(status_code=404, detail=f"Comparison {comparison_id} not found")
    return comparison


@qa_router.post("/generate-tests")
def generate_tests(payload: GenerateTestsRequest) -> Dict[str, Any]:
    """Generate test cases without running them."""
    cases = generate_test_cases(
        payload.category,
        count=payload.count,
        difficulty=payload.difficulty,  # type: ignore
        subcategory=payload.subcategory,
        custom_prompts=payload.custom_prompts,
    )

    # Optionally generate adversarial variants
    if payload.include_adversarial_variants:
        variants: List[TestCase] = []
        for case in cases[:3]:
            variants.extend(generate_adversarial_variants(case.prompt))
        cases.extend(variants)

    return {
        "category": payload.category,
        "test_cases": [
            {
                "test_id": tc.test_id,
                "prompt": tc.prompt,
                "category": tc.category,
                "subcategory": tc.subcategory,
                "difficulty": tc.difficulty,
                "expected_behavior": tc.expected_behavior,
                "expected_refusal": tc.expected_refusal,
                "tags": tc.tags,
            }
            for tc in cases
        ],
        "count": len(cases),
    }


@qa_router.get("/models")
def list_available_models() -> Dict[str, Any]:
    """List available Abacus.AI models for testing."""
    return {
        "models": [
            {"id": "CLAUDE_V3_5_SONNET", "provider": "abacusai", "description": "Anthropic Claude 3.5 Sonnet"},
            {"id": "CLAUDE_V3_HAIKU", "provider": "abacusai", "description": "Anthropic Claude 3 Haiku"},
            {"id": "CLAUDE_V3_OPUS", "provider": "abacusai", "description": "Anthropic Claude 3 Opus"},
            {"id": "GPT4O", "provider": "abacusai", "description": "OpenAI GPT-4o"},
            {"id": "GPT4O_MINI", "provider": "abacusai", "description": "OpenAI GPT-4o Mini"},
            {"id": "GEMINI_1_5_PRO", "provider": "abacusai", "description": "Google Gemini 1.5 Pro"},
            {"id": "LLAMA3_1_405B", "provider": "abacusai", "description": "Meta Llama 3.1 405B"},
            {"id": "LLAMA3_1_70B", "provider": "abacusai", "description": "Meta Llama 3.1 70B"},
        ],
        "note": "Use model 'id' as model_id in test requests. Set provider to 'mock' for dry-run testing.",
    }
