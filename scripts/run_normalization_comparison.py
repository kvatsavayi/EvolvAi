#!/usr/bin/env python3
"""
Normalization Comparison: Before vs After
==========================================
Runs the same 32 tests with both:
  1. Legacy rule-based validators (before)
  2. Hybrid judge with LLM normalization (after)

Generates a comparison report showing improvement.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.qa.engine import QAEngine
from core.qa.hybrid_judge import HybridJudgeConfig
from core.qa.models import MUTConfig, TestCase, TestSuite, ModelTestReport

from scripts.run_real_qa_tests import (
    MODELS,
    build_capability_tests,
    build_safety_tests,
    build_adversarial_tests,
)


def progress(idx, total, result):
    status = "✅" if result.passed else "❌"
    print(f"  [{idx:2d}/{total}] {status} {result.category:12s} | {result.prompt[:55]}...", flush=True)


def run_comparison():
    print("=" * 70, flush=True)
    print("  Normalization Comparison: Before vs After", flush=True)
    print("=" * 70, flush=True)

    capability_tests = build_capability_tests()
    safety_tests = build_safety_tests()
    adversarial_tests = build_adversarial_tests()
    all_tests = capability_tests + safety_tests + adversarial_tests

    suite = TestSuite(
        name="normalization_comparison_v1",
        description=f"Comparison suite: {len(all_tests)} tests",
        test_cases=all_tests,
    )

    print(f"\n📋 {len(all_tests)} tests ({len(capability_tests)} cap, {len(safety_tests)} safety, {len(adversarial_tests)} adv)\n", flush=True)

    # Legacy engine (rule-based only)
    engine_legacy = QAEngine(use_llm_judge=False, use_hybrid_judge=False)

    # Hybrid engine: normalizer + rules (no LLM judge to keep it fast)
    hybrid_config = HybridJudgeConfig(
        enable_normalizer=True,
        enable_llm_judge=False,  # Skip LLM judge for speed; normalizer does the heavy lifting
        enable_rule_validators=True,
        safety_fast_path=True,
    )
    engine_hybrid = QAEngine(use_hybrid_judge=True, hybrid_config=hybrid_config)

    legacy_results: Dict[str, ModelTestReport] = {}
    hybrid_results: Dict[str, ModelTestReport] = {}

    for model in MODELS:
        name = model.metadata.get('display_name', model.model_id)
        model_start = time.time()

        print(f"{'─' * 70}", flush=True)
        print(f"🤖 {name} — Legacy...", flush=True)
        leg = engine_legacy.run_test_suite(model, suite, progress_callback=progress)
        legacy_results[model.model_id] = leg
        print(f"  📊 Legacy: {leg.pass_rate:.1%} ({leg.passed_tests}/{leg.total_tests})", flush=True)

        print(f"\n  🔄 {name} — Hybrid (normalizer)...", flush=True)
        hyb = engine_hybrid.run_test_suite(model, suite, progress_callback=progress)
        hybrid_results[model.model_id] = hyb
        print(f"  📊 Hybrid: {hyb.pass_rate:.1%} ({hyb.passed_tests}/{hyb.total_tests})", flush=True)

        elapsed = time.time() - model_start
        diff = hyb.pass_rate - leg.pass_rate
        print(f"  ⏱️  {elapsed:.1f}s | Change: {'+' if diff >= 0 else ''}{diff:.1%}\n", flush=True)

    return suite, legacy_results, hybrid_results, all_tests


def generate_report(suite, legacy_results, hybrid_results, all_tests, elapsed):
    lines = []
    lines.append("# LLM Normalization Comparison Report")
    lines.append(f"\n**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Total Tests per Model:** {len(all_tests)}")
    lines.append(f"**Models Tested:** {', '.join(legacy_results.keys())}")
    lines.append(f"**Execution Time:** {elapsed:.0f}s")
    lines.append("")

    # Architecture
    lines.append("## Architecture: Three-Layer Evaluation\n")
    lines.append("```")
    lines.append("┌──────────────────────────────────────────────┐")
    lines.append("│              Model Response                   │")
    lines.append("└─────────────────┬────────────────────────────┘")
    lines.append("                  │")
    lines.append("    ┌─────────────▼─────────────┐")
    lines.append("    │  Layer 1: Safety Rules     │  Fast, deterministic")
    lines.append("    │  (toxicity, PII, bias)     │  ← FAIL = stop here")
    lines.append("    └─────────────┬──────────────┘")
    lines.append("                  │ PASS")
    lines.append("    ┌─────────────▼─────────────┐")
    lines.append("    │  Layer 2: LLM Normalizer   │  Format conversion")
    lines.append("    │  (9,386 → 9386, etc.)     │  ← Fixes false negatives")
    lines.append("    └─────────────┬──────────────┘")
    lines.append("                  │")
    lines.append("    ┌─────────────▼─────────────┐")
    lines.append("    │  Layer 3: LLM-as-Judge     │  Semantic evaluation")
    lines.append("    │  (multi-dimensional)       │  ← Final arbiter")
    lines.append("    └────────────────────────────┘")
    lines.append("```\n")

    # Summary
    lines.append("## Overall Results\n")
    lines.append("| Model | Legacy Pass Rate | Hybrid Pass Rate | Improvement | Fixed | Regressed |")
    lines.append("|-------|-----------------|-----------------|-------------|-------|-----------|")

    all_fixed = {}
    all_regressed = {}

    for mid in legacy_results:
        leg = legacy_results[mid]
        hyb = hybrid_results[mid]
        improvement = hyb.pass_rate - leg.pass_rate

        fixed = []
        regressed = []
        for i in range(min(len(leg.results), len(hyb.results))):
            tc = suite.test_cases[i]
            if not leg.results[i].passed and hyb.results[i].passed:
                fixed.append((tc, leg.results[i], hyb.results[i]))
            elif leg.results[i].passed and not hyb.results[i].passed:
                regressed.append((tc, leg.results[i], hyb.results[i]))

        all_fixed[mid] = fixed
        all_regressed[mid] = regressed

        imp = f"+{improvement:.1%}" if improvement > 0 else f"{improvement:.1%}"
        lines.append(
            f"| **{mid}** | {leg.pass_rate:.1%} ({leg.passed_tests}/{leg.total_tests}) "
            f"| {hyb.pass_rate:.1%} ({hyb.passed_tests}/{hyb.total_tests}) "
            f"| {imp} | {len(fixed)} | {len(regressed)} |"
        )

    total_fixed = sum(len(v) for v in all_fixed.values())
    total_regressed = sum(len(v) for v in all_regressed.values())
    lines.append("")
    lines.append(f"**Total false negatives fixed:** {total_fixed}")
    lines.append(f"**Total regressions:** {total_regressed}")
    lines.append("")

    # Category breakdown
    lines.append("## Category Breakdown\n")
    categories = sorted(set(tc.category for tc in suite.test_cases))
    for cat in categories:
        lines.append(f"### {cat.title()} Tests\n")
        lines.append("| Model | Legacy | Hybrid | Change |")
        lines.append("|-------|--------|--------|--------|")
        for mid in legacy_results:
            lc = legacy_results[mid].category_scores.get(cat, {})
            hc = hybrid_results[mid].category_scores.get(cat, {})
            lp = lc.get("pass_rate", 0)
            hp = hc.get("pass_rate", 0)
            ch = hp - lp
            cs = f"+{ch:.1%}" if ch > 0 else f"{ch:.1%}" if ch < 0 else "—"
            lines.append(f"| {mid} | {lp:.1%} | {hp:.1%} | {cs} |")
        lines.append("")

    # Fixed false negatives
    lines.append("## Fixed False Negatives (❌ → ✅)\n")
    lines.append("Tests that previously failed due to formatting/matching but now pass with normalization.\n")
    any_fixed = False
    for mid, cases in all_fixed.items():
        if cases:
            any_fixed = True
            lines.append(f"### {mid}\n")
            for tc, lr, hr in cases:
                lines.append(f"#### ✅ Fixed: [{tc.category}/{tc.subcategory}]")
                lines.append(f"- **Prompt:** {tc.prompt[:150]}")
                if tc.expected_answer:
                    lines.append(f"- **Expected:** `{tc.expected_answer}`")
                lines.append(f"- **Response:** `{lr.response[:250]}`")
                lines.append(f"- **Legacy failures:** {', '.join(lr.failures) or 'none'}")
                lines.append(f"- **Legacy correctness:** {lr.scores.get('correctness', 'N/A')}")
                lines.append(f"- **Hybrid correctness:** {hr.scores.get('correctness', 'N/A')}")
                # Show normalization details
                norm_details = hr.validator_details.get("correctness_normalized", {})
                if norm_details:
                    det = norm_details.get("details", {})
                    lines.append(f"- **Normalization reason:** {det.get('normalization_reason', 'N/A')}")
                lines.append("")
    if not any_fixed:
        lines.append("*No false negatives were fixed.*\n")

    # Regressions
    lines.append("## Regressions (✅ → ❌)\n")
    any_reg = False
    for mid, cases in all_regressed.items():
        if cases:
            any_reg = True
            lines.append(f"### {mid}\n")
            for tc, lr, hr in cases:
                lines.append(f"#### ❌ Regressed: [{tc.category}/{tc.subcategory}]")
                lines.append(f"- **Prompt:** {tc.prompt[:150]}")
                lines.append(f"- **Hybrid failures:** {', '.join(hr.failures)}")
                lines.append("")
    if not any_reg:
        lines.append("✅ *No regressions introduced.*\n")

    # Key insights
    lines.append("## Key Insights\n")
    lines.append("### Why Normalization Helps\n")
    lines.append("| Problem | Example | How Normalizer Fixes It |")
    lines.append("|---------|---------|------------------------|")
    lines.append("| Number formatting | `9,386` vs `9386` | Strips commas, compares numerically |")
    lines.append("| Verbose answers | `x = 5` vs `5` | Extracts core numeric value |")
    lines.append("| Math notation | `x^3/3 + C` vs `x^3/3` | Semantic equivalence check |")
    lines.append("| Code fences | `` ```python\\ndef f()...``` `` vs `def f()...` | Extracts code from blocks |")
    lines.append("| Case differences | `Canberra` vs `canberra` | Case-insensitive comparison |")
    lines.append("| Units in answer | `80 km/h` vs `80` | Extracts numeric value |")
    lines.append("")

    lines.append("### When to Use Each Layer\n")
    lines.append("| Layer | Use When | Speed | Cost |")
    lines.append("|-------|----------|-------|------|")
    lines.append("| Safety Rules | Always (first check) | <1ms | Free |")
    lines.append("| LLM Normalizer | Expected answer exists | <1ms (local) | Free (local) |")
    lines.append("| LLM-as-Judge | Ambiguous cases, quality scoring | 2-3s | API call |")
    lines.append("")

    return "\n".join(lines)


def main():
    start = time.time()
    suite, legacy_results, hybrid_results, all_tests = run_comparison()
    elapsed = time.time() - start

    # Print summary
    print(f"\n{'=' * 70}", flush=True)
    print("  FINAL COMPARISON", flush=True)
    print(f"{'=' * 70}", flush=True)
    for mid in legacy_results:
        leg = legacy_results[mid]
        hyb = hybrid_results[mid]
        diff = hyb.pass_rate - leg.pass_rate
        print(f"  {mid}: {leg.pass_rate:.1%} → {hyb.pass_rate:.1%} ({'+' if diff >= 0 else ''}{diff:.1%})", flush=True)

    # Generate report
    report = generate_report(suite, legacy_results, hybrid_results, all_tests, elapsed)
    os.makedirs(os.path.join(PROJECT_ROOT, "data"), exist_ok=True)

    report_path = os.path.join(PROJECT_ROOT, "data", "NORMALIZATION_COMPARISON.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\n📝 Report: {report_path}", flush=True)

    json_path = os.path.join(PROJECT_ROOT, "data", "normalization_comparison.json")
    json_data = {
        "execution_time_seconds": round(elapsed, 2),
        "models": list(legacy_results.keys()),
        "test_count": len(all_tests),
        "legacy": {mid: {"pass_rate": r.pass_rate, "passed": r.passed_tests, "total": r.total_tests, "category_scores": r.category_scores} for mid, r in legacy_results.items()},
        "hybrid": {mid: {"pass_rate": r.pass_rate, "passed": r.passed_tests, "total": r.total_tests, "category_scores": r.category_scores} for mid, r in hybrid_results.items()},
    }
    with open(json_path, "w") as f:
        json.dump(json_data, f, indent=2, default=str)
    print(f"💾 JSON: {json_path}", flush=True)
    print(f"\n✅ Done in {elapsed:.0f}s", flush=True)


if __name__ == "__main__":
    main()
