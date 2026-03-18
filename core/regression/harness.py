from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from core.observability.canonical import canonical_sha256
from core.pod.dna import DNA
from core.snapshot.builder import build_snapshot
from core.snapshot.schema import SnapshotPolicies


@dataclass
class RegressionCase:
    name: str
    user_input: str
    request_type: str
    invariants: Dict[str, Any]


class RegressionHarness:
    def __init__(self, golden_path: Path) -> None:
        self.golden_path = golden_path
        self.cases = self._load_cases(golden_path)

    def _load_cases(self, golden_path: Path) -> List[RegressionCase]:
        if not golden_path.exists():
            return []
        payload = json.loads(golden_path.read_text(encoding="utf-8"))
        cases: List[RegressionCase] = []
        for row in payload.get("cases", []):
            cases.append(
                RegressionCase(
                    name=str(row["name"]),
                    user_input=str(row["user_input"]),
                    request_type=str(row.get("request_type", "general")),
                    invariants=dict(row.get("invariants", {})),
                )
            )
        return cases

    def run_for_pod_dna(self, *, pod: Any, dna: DNA) -> Dict[str, Any]:
        if not self.cases:
            return {"passed": True, "cases": [], "summary": "no-golden-cases"}

        previous_dna = pod.dna
        pod.dna = dna
        case_reports = []
        try:
            for case in self.cases:
                policies = SnapshotPolicies(
                    tool_policy_id="tp_regression",
                    allowed_tools=["http_get", "fs_read"],
                    forbidden_tools=["shell_exec", "fs_write"],
                    budgets={"max_total_tool_calls": 3, "max_http_get": 2},
                )
                snapshot = build_snapshot(
                    request_id=f"reg_{case.name}",
                    user_input=case.user_input,
                    request_type=case.request_type,
                    policies=policies,
                    context_state={},
                )
                run = pod.run(snapshot)
                judge_result = run["judge_result"]
                failures = judge_result.get("failures", [])
                failure_codes = [f.get("code") for f in failures]

                expect_pass = bool(case.invariants.get("expect_pass", True))
                require_no_unsafe = bool(case.invariants.get("require_no_unsafe_tool", True))
                require_no_ungrounded = bool(case.invariants.get("require_no_ungrounded_claims", True))

                ok = True
                if expect_pass and not bool(judge_result.get("pass", False)):
                    ok = False
                if require_no_unsafe and "UNSAFE_TOOL" in failure_codes:
                    ok = False
                if require_no_ungrounded and "UNGROUNDED_CLAIM" in failure_codes:
                    ok = False

                case_reports.append(
                    {
                        "case": case.name,
                        "request_type": case.request_type,
                        "run_id": run["run_id"],
                        "ok": ok,
                        "judge_pass": bool(judge_result.get("pass", False)),
                        "failure_codes": failure_codes,
                    }
                )
        finally:
            pod.dna = previous_dna

        passed = all(c["ok"] for c in case_reports)
        return {
            "passed": passed,
            "cases": case_reports,
            "summary": {
                "total": len(case_reports),
                "passed": sum(1 for c in case_reports if c["ok"]),
                "failed": sum(1 for c in case_reports if not c["ok"]),
            },
            "report_hash": canonical_sha256(case_reports),
        }
