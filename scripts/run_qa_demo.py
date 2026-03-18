#!/usr/bin/env python3
"""
EvolvAi LLM QA System – Demo Script
=====================================

Demonstrates the QA system by:
1. Testing 3 different models (mock + 2 real Abacus.AI models)
2. Running a test suite across 4 persona categories
3. Showing attractor discovery
4. Generating a comparison report
5. Running a regression check

Usage:
    # Mock mode (no API calls):
    python scripts/run_qa_demo.py --mock

    # Live mode (uses Abacus.AI API):
    python scripts/run_qa_demo.py

    # Quick mode (fewer tests):
    python scripts/run_qa_demo.py --quick
"""
from __future__ import annotations

import argparse
import json
import sys
import os
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.qa.engine import QAEngine
from core.qa.models import MUTConfig, TestCase
from core.qa.test_generator import generate_test_suite, generate_adversarial_variants


def _banner(msg: str) -> None:
    width = max(60, len(msg) + 4)
    print("\n" + "=" * width)
    print(f"  {msg}")
    print("=" * width)


def _progress(current: int, total: int, result: Any) -> None:
    status = "✓" if result.passed else "✗"
    print(f"  [{current}/{total}] {status} {result.category}/{result.test_id[:16]} — {', '.join(result.failures[:2]) or 'passed'}")


def run_demo(*, mock: bool = True, quick: bool = False) -> None:
    count_per_cat = 2 if quick else 4

    # -------------------------------------------------------------------
    # 1. Define models under test
    # -------------------------------------------------------------------
    _banner("1. Configuring Models Under Test")

    if mock:
        models = [
            MUTConfig(model_id="mock-model-A", provider="mock", metadata={"display_name": "Mock Model A"}),
            MUTConfig(model_id="mock-model-B", provider="mock", metadata={"display_name": "Mock Model B"}),
            MUTConfig(model_id="mock-model-C", provider="mock", metadata={"display_name": "Mock Model C"}),
        ]
    else:
        models = [
            MUTConfig(model_id="CLAUDE_V3_5_SONNET", provider="abacusai", metadata={"display_name": "Claude 3.5 Sonnet"}),
            MUTConfig(model_id="GPT4O_MINI", provider="abacusai", metadata={"display_name": "GPT-4o Mini"}),
            MUTConfig(model_id="GEMINI_1_5_PRO", provider="abacusai", metadata={"display_name": "Gemini 1.5 Pro"}),
        ]

    for m in models:
        print(f"  → {m.display_name} ({m.model_id}, provider={m.provider})")

    # -------------------------------------------------------------------
    # 2. Generate test suite
    # -------------------------------------------------------------------
    _banner("2. Generating Test Suite")

    suite = generate_test_suite(
        name="EvolvAi QA Demo Suite",
        categories=["adversarial", "safety", "capability", "regression"],
        count_per_category=count_per_cat,
        include_adversarial_variants=not quick,
    )
    print(f"  Generated {len(suite.test_cases)} test cases")
    cats = {}
    for tc in suite.test_cases:
        cats[tc.category] = cats.get(tc.category, 0) + 1
    for cat, cnt in sorted(cats.items()):
        print(f"    {cat}: {cnt} tests")

    # -------------------------------------------------------------------
    # 3. Test each model individually
    # -------------------------------------------------------------------
    _banner("3. Testing Individual Models")

    engine = QAEngine(use_llm_judge=False, consistency_runs=1)
    reports = {}

    for mut in models:
        print(f"\n  Testing: {mut.display_name}")
        report = engine.run_test_suite(mut, suite, progress_callback=_progress)
        reports[mut.model_id] = report
        print(f"\n  Results for {mut.display_name}:")
        print(f"    Pass rate: {report.pass_rate:.1%} ({report.passed_tests}/{report.total_tests})")
        print(f"    Avg latency: {report.avg_latency_ms:.0f}ms")
        if report.category_scores:
            for cat, scores in report.category_scores.items():
                print(f"    {cat}: pass_rate={scores.get('pass_rate', 0):.1%}, count={scores.get('count', 0)}")

    # -------------------------------------------------------------------
    # 4. Attractor Discovery
    # -------------------------------------------------------------------
    _banner("4. Attractor Discovery (Failure Patterns)")

    for mid, rpt in reports.items():
        if rpt.attractors:
            print(f"\n  {mid}:")
            for attr in rpt.attractors:
                print(f"    ⚠ Pattern: {attr['pattern']} (count={attr['count']}, severity={attr['severity']})")
        else:
            print(f"\n  {mid}: No recurring failure patterns found")

    # -------------------------------------------------------------------
    # 5. Model Comparison
    # -------------------------------------------------------------------
    _banner("5. Model Comparison")

    comparison = engine.compare_models(models, suite)
    print("\n  Rankings:")
    for idx, rank in enumerate(comparison.rankings, 1):
        print(f"    #{idx} {rank['model_id']} — pass_rate={rank['pass_rate']:.1%}, "
              f"latency={rank['avg_latency_ms']:.0f}ms, attractors={rank['attractors']}")

    # -------------------------------------------------------------------
    # 6. Regression Check (model A as baseline, model B as new)
    # -------------------------------------------------------------------
    _banner("6. Regression Check")

    if len(models) >= 2:
        baseline_mut = models[0]
        new_mut = models[1]
        print(f"  Baseline: {baseline_mut.display_name}")
        print(f"  New:      {new_mut.display_name}")

        baseline_report = reports[baseline_mut.model_id]
        baseline_map = {r.test_id: r for r in baseline_report.results}

        new_report = engine.run_test_suite(new_mut, suite, baseline_results=baseline_map)

        regressions = []
        improvements = []
        for nr in new_report.results:
            br = baseline_map.get(nr.test_id)
            if br and br.passed and not nr.passed:
                regressions.append(nr)
            elif br and not br.passed and nr.passed:
                improvements.append(nr)

        print(f"\n  Regressions: {len(regressions)}")
        for r in regressions[:5]:
            print(f"    ✗ {r.test_id[:16]} — {r.prompt[:60]}...")
        print(f"  Improvements: {len(improvements)}")
        for r in improvements[:5]:
            print(f"    ✓ {r.test_id[:16]} — {r.prompt[:60]}...")
        verdict = "⚠ REGRESSION DETECTED" if regressions else "✓ NO REGRESSION"
        print(f"\n  Verdict: {verdict}")

    # -------------------------------------------------------------------
    # 7. Summary Report
    # -------------------------------------------------------------------
    _banner("7. QA Summary Report")

    output_path = ROOT / "data" / "qa_demo_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    full_report = {
        "demo_run_at": str(os.popen("date -u").read().strip()),
        "mode": "mock" if mock else "live",
        "models_tested": [m.model_id for m in models],
        "suite": {
            "name": suite.name,
            "total_tests": len(suite.test_cases),
        },
        "model_reports": {
            mid: QAEngine.report_to_dict(rpt) for mid, rpt in reports.items()
        },
        "comparison": comparison.to_dict(),
    }

    output_path.write_text(json.dumps(full_report, indent=2, default=str))
    print(f"\n  Full report written to: {output_path}")
    print(f"  Models tested: {len(models)}")
    print(f"  Total test runs: {sum(r.total_tests for r in reports.values())}")
    print(f"  Best model: {comparison.rankings[0]['model_id'] if comparison.rankings else 'N/A'}")

    _banner("Demo Complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EvolvAi QA System Demo")
    parser.add_argument("--mock", action="store_true", default=False, help="Use mock models (no API calls)")
    parser.add_argument("--quick", action="store_true", default=False, help="Quick mode (fewer tests)")
    parser.add_argument("--live", action="store_true", default=False, help="Use live Abacus.AI models")
    args = parser.parse_args()
    run_demo(mock=not args.live, quick=args.quick)
