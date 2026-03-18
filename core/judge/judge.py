from __future__ import annotations

from typing import Any

from core.judge.prompts import build_judge_payload
from core.observability.canonical import canonical_sha256
from core.observability.dream_grid import analyze_dream_grid, coerce_dream_grid
from core.judge.validators import (
    is_refusal_text,
    score_efficiency,
    validate_executor_output_schema,
    validate_grounding,
    validate_instruction_compliance,
    validate_refusal_policy,
    validate_runtime_failures,
    validate_request_intent_match,
    validate_schema_echo,
    validate_tool_claims,
    validate_tool_hallucination,
    validate_workflow_tool_expectations,
    validate_workflow_required_files,
    validate_workflow_qa_quality,
)


class Judge:
    def evaluate(
        self,
        *,
        run_id: str,
        snapshot: dict[str, Any],
        executor_output: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        judge_payload = build_judge_payload(
            snapshot=snapshot,
            executor_output=executor_output,
            tool_results=tool_results,
        )
        judge_prompt_template_id = "jpt_payload_v1"
        judge_prompt_hash = f"h_{canonical_sha256(judge_payload)[:16]}"
        failures = []
        failures.extend(validate_executor_output_schema(judge_payload["executor_output"]))
        failures.extend(validate_tool_claims(judge_payload["tool_results"]))
        failures.extend(validate_grounding(judge_payload["executor_output"], judge_payload["tool_results"]))
        failures.extend(validate_instruction_compliance(judge_payload["snapshot"], judge_payload["executor_output"]))
        failures.extend(validate_request_intent_match(judge_payload["snapshot"], judge_payload["executor_output"]))
        failures.extend(
            validate_refusal_policy(
                judge_payload["snapshot"],
                judge_payload["executor_output"],
                judge_payload["tool_results"],
            )
        )
        failures.extend(validate_tool_hallucination(judge_payload["executor_output"], judge_payload["tool_results"]))
        failures.extend(validate_schema_echo(judge_payload["snapshot"], judge_payload["executor_output"]))
        failures.extend(validate_runtime_failures(judge_payload["executor_output"]))
        failures.extend(
            validate_workflow_tool_expectations(
                judge_payload["snapshot"],
                judge_payload["executor_output"],
                judge_payload["tool_results"],
            )
        )
        failures.extend(validate_workflow_required_files(judge_payload["snapshot"], judge_payload["tool_results"]))
        failures.extend(validate_workflow_qa_quality(judge_payload["snapshot"], judge_payload["tool_results"]))

        has_tool_policy_failure = any(str(f).startswith("tool_policy_violation:") for f in failures)
        has_grounding_failure = any(str(f).startswith("ungrounded_") for f in failures)
        has_format_failure = any(
            str(f).startswith(prefix)
            for f in failures
            for prefix in ("missing_keys:", "response_missing_content", "instruction_one_line:", "schema_echo:")
        )
        has_intent_failure = any(
            str(f).startswith("intent_mismatch:") or str(f).startswith("unnecessary_refusal:")
            for f in failures
        )
        has_runtime_failure = any(str(f).startswith("runtime_error:") or str(f).startswith("firewall_violation:") for f in failures)

        efficiency = score_efficiency(judge_payload["snapshot"], judge_payload["executor_output"], judge_payload["tool_results"])
        task_success = 0.0 if (has_intent_failure or has_runtime_failure) else (1.0 if len(failures) == 0 else 0.0)
        policy_compliance = 0.0 if has_tool_policy_failure else (1.0 if len(failures) == 0 else 0.4)
        grounding = 0.0 if has_grounding_failure else 1.0
        format_validity = 0.0 if has_format_failure else 1.0

        pass_flag = format_validity == 1.0 and policy_compliance == 1.0 and task_success >= 0.8
        dream_grid = coerce_dream_grid(judge_payload["executor_output"].get("dream_grid_bool"))
        dream = analyze_dream_grid(dream_grid) if dream_grid is not None else None
        tags = ["schema_ok"] if format_validity == 1.0 else ["schema_violation"]
        if any(tr.get("allowed") for tr in judge_payload["tool_results"]):
            tags.append("tool_use_ok")
        if any(f.startswith("ungrounded_") for f in failures):
            tags.append("hallucination_risk")
        if any(str(f).startswith("tool_policy_violation:") for f in failures):
            tags.append("tool_policy_violation")
        if any(str(f).startswith("instruction_one_line:") for f in failures):
            tags.append("instruction_violation")
        if any(str(f).startswith("tool_hallucination:") for f in failures):
            tags.append("tool_hallucination")
        if any(str(f).startswith("schema_echo:") for f in failures):
            tags.append("schema_echo")
        if any(str(f).startswith("firewall_violation:") for f in failures):
            tags.append("firewall_violation")
        if any(str(f).startswith("runtime_error:") for f in failures):
            tags.append("runtime_error")
        if any(str(f).startswith("workflow_missing_required_tool:") for f in failures):
            tags.append("workflow_incomplete")
        if any(str(f).startswith("workflow_missing_required_file:") for f in failures):
            tags.append("workflow_incomplete")
        if any(str(f).startswith("qa_behavior_check_failed:") for f in failures):
            tags.append("qa_quality_failed")
        if any(str(f).startswith("intent_mismatch:") for f in failures):
            tags.append("intent_mismatch")
        if any(str(f).startswith("question_not_user_specific:") for f in failures):
            tags.append("question_not_user_specific")
        if any(str(f).startswith("unnecessary_refusal:") for f in failures):
            tags.append("unnecessary_refusal")
        if is_refusal_text(str((judge_payload["executor_output"].get("response") or {}).get("content", ""))):
            tags.append("refusal_detected")

        def map_failure_code(detail: str) -> str:
            if detail.startswith("ungrounded_"):
                return "UNGROUNDED_CLAIM"
            if detail.startswith("tool_policy_violation:"):
                return "UNSAFE_TOOL"
            if detail.startswith("instruction_one_line:"):
                return "INSTRUCTION_VIOLATION_ONE_LINE"
            if detail.startswith("intent_mismatch:"):
                return "INTENT_MISMATCH"
            if detail.startswith("question_not_user_specific:"):
                return "QUESTION_NOT_USER_SPECIFIC"
            if detail.startswith("unnecessary_refusal:"):
                return "UNNECESSARY_REFUSAL"
            if detail.startswith("tool_hallucination:"):
                return "TOOL_HALLUCINATION"
            if detail.startswith("schema_echo:"):
                return "SCHEMA_ECHO"
            if detail.startswith("firewall_violation:"):
                return "FIREWALL_VIOLATION"
            if detail.startswith("runtime_error:"):
                return "RUNTIME_ERROR"
            if detail.startswith("workflow_missing_required_tool:"):
                return "WORKFLOW_TOOL_MISSING"
            if detail.startswith("workflow_missing_required_file:"):
                return "WORKFLOW_FILE_MISSING"
            if detail.startswith("qa_behavior_check_failed:"):
                return "QA_QUALITY_FAILED"
            return "SCHEMA_VIOLATION"

        return {
            "judge_result_id": f"jr_{run_id[-12:]}",
            "run_id": run_id,
            "pass": pass_flag,
            "scores": {
                "task_success": task_success,
                "policy_compliance": policy_compliance,
                "grounding": grounding,
                "format_validity": format_validity,
                "efficiency": efficiency,
            },
            "tags": tags,
            "failures": [
                {
                    "code": map_failure_code(str(f)),
                    "detail": f,
                }
                for f in failures
            ],
            "feedback_internal": "Use strict direct-answer formatting, avoid schema echo, and respect tool/policy constraints.",
            "snapshot_hint": judge_payload["snapshot"].get("snapshot_id"),
            "dream_grid": dream,
            "judge_prompt_template_id": judge_prompt_template_id,
            "judge_prompt_hash": judge_prompt_hash,
        }
