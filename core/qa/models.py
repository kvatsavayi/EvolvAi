"""Data models for the LLM QA system – MUT config, test cases, results."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
import hashlib, json, uuid


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Model Under Test (MUT) configuration
# ---------------------------------------------------------------------------

@dataclass
class MUTConfig:
    """Describes one model to be tested."""
    model_id: str                             # e.g. "CLAUDE_V3_5_SONNET"
    provider: str = "abacusai"                # abacusai | mock | ollama | remote
    temperature: float = 0.2
    max_tokens: int = 1024
    top_p: Optional[float] = None
    system_message: str = "You are a helpful AI assistant."
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.metadata.get("display_name", self.model_id)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    """A single test case to run against the MUT."""
    test_id: str = field(default_factory=lambda: f"tc_{uuid.uuid4().hex[:12]}")
    prompt: str = ""
    category: str = "general"                 # safety | capability | adversarial | regression
    subcategory: str = ""                     # e.g. "math", "jailbreak", "bias"
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    expected_behavior: str = ""               # Human-readable expectation
    expected_answer: Optional[str] = None     # For correctness checks (exact or substring)
    expected_refusal: bool = False            # True if model SHOULD refuse
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TestSuite:
    """A collection of test cases."""
    suite_id: str = field(default_factory=lambda: f"ts_{uuid.uuid4().hex[:12]}")
    name: str = ""
    description: str = ""
    test_cases: List[TestCase] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    tags: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Test results
# ---------------------------------------------------------------------------

@dataclass
class TestCaseResult:
    """Result of running a single test case against one MUT."""
    result_id: str = field(default_factory=lambda: f"tcr_{uuid.uuid4().hex[:12]}")
    test_id: str = ""
    model_id: str = ""
    prompt: str = ""
    response: str = ""
    latency_ms: int = 0
    token_counts: Dict[str, int] = field(default_factory=dict)

    # Validation results
    passed: bool = True
    scores: Dict[str, float] = field(default_factory=dict)   # e.g. {"safety": 1.0, "correctness": 0.8}
    failures: List[str] = field(default_factory=list)
    validator_details: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    created_at: str = field(default_factory=_utc_now)
    category: str = ""
    tags: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ModelTestReport:
    """Aggregate report for one model across a test suite."""
    report_id: str = field(default_factory=lambda: f"rpt_{uuid.uuid4().hex[:12]}")
    model_id: str = ""
    suite_id: str = ""
    results: List[TestCaseResult] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)

    # Aggregates (computed)
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    pass_rate: float = 0.0
    avg_latency_ms: float = 0.0
    category_scores: Dict[str, Dict[str, float]] = field(default_factory=dict)
    attractors: List[Dict[str, Any]] = field(default_factory=list)   # failure patterns

    def compute_aggregates(self) -> None:
        self.total_tests = len(self.results)
        self.passed_tests = sum(1 for r in self.results if r.passed)
        self.failed_tests = self.total_tests - self.passed_tests
        self.pass_rate = (self.passed_tests / self.total_tests) if self.total_tests else 0.0
        latencies = [r.latency_ms for r in self.results if r.latency_ms > 0]
        self.avg_latency_ms = (sum(latencies) / len(latencies)) if latencies else 0.0

        # Per-category scores
        cat_map: Dict[str, List[TestCaseResult]] = {}
        for r in self.results:
            cat_map.setdefault(r.category or "general", []).append(r)
        self.category_scores = {}
        for cat, cat_results in cat_map.items():
            cat_total = len(cat_results)
            cat_passed = sum(1 for r in cat_results if r.passed)
            avg_scores: Dict[str, float] = {}
            for r in cat_results:
                for k, v in r.scores.items():
                    avg_scores.setdefault(k, 0.0)
                    avg_scores[k] += v
            for k in avg_scores:
                avg_scores[k] = round(avg_scores[k] / cat_total, 4)
            self.category_scores[cat] = {
                "pass_rate": round(cat_passed / cat_total, 4) if cat_total else 0.0,
                "count": cat_total,
                **avg_scores,
            }

        # Discover attractors (repeated failure patterns)
        failure_counts: Dict[str, int] = {}
        for r in self.results:
            for f in r.failures:
                key = f.split(":")[0] if ":" in f else f
                failure_counts[key] = failure_counts.get(key, 0) + 1
        self.attractors = [
            {"pattern": pattern, "count": count, "severity": "high" if count >= 3 else "medium"}
            for pattern, count in sorted(failure_counts.items(), key=lambda x: -x[1])
            if count >= 2
        ]


@dataclass
class ComparisonReport:
    """Compare multiple models on the same test suite."""
    comparison_id: str = field(default_factory=lambda: f"cmp_{uuid.uuid4().hex[:12]}")
    suite_id: str = ""
    model_reports: Dict[str, ModelTestReport] = field(default_factory=dict)  # model_id -> report
    created_at: str = field(default_factory=_utc_now)

    @property
    def rankings(self) -> List[Dict[str, Any]]:
        """Rank models by pass rate, then avg latency."""
        items = []
        for mid, rpt in self.model_reports.items():
            items.append({
                "model_id": mid,
                "pass_rate": rpt.pass_rate,
                "avg_latency_ms": rpt.avg_latency_ms,
                "total_tests": rpt.total_tests,
                "attractors": len(rpt.attractors),
            })
        return sorted(items, key=lambda x: (-x["pass_rate"], x["avg_latency_ms"]))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "comparison_id": self.comparison_id,
            "suite_id": self.suite_id,
            "created_at": self.created_at,
            "rankings": self.rankings,
            "model_reports": {
                mid: {
                    "report_id": rpt.report_id,
                    "model_id": rpt.model_id,
                    "total_tests": rpt.total_tests,
                    "passed_tests": rpt.passed_tests,
                    "failed_tests": rpt.failed_tests,
                    "pass_rate": rpt.pass_rate,
                    "avg_latency_ms": rpt.avg_latency_ms,
                    "category_scores": rpt.category_scores,
                    "attractors": rpt.attractors,
                }
                for mid, rpt in self.model_reports.items()
            },
        }
