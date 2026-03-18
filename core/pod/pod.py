from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from core.executor.runner import ExecutorRunner
from core.executor.prompts import build_executor_prompt, executor_prompt_hash, executor_prompt_template_id
from core.executor.sandbox import build_safe_retry_message
from core.judge.judge import Judge
from core.observability.canonical import canonical_sha256
from core.observability.dream_grid import coerce_dream_grid
from core.observability.traces import (
    behavior_fingerprint,
    canonical_content_hash,
    trace_fingerprint,
    tool_sequence_fingerprint,
)
from core.pod.dna import DNA, make_dna_hash, seed_dna
from core.pod.lineage import make_lineage_edge, make_run_id, utc_now_iso
from core.pod.mutation import mutate_dna
from core.pod.selection import select_by_pass_rate
from core.regression.harness import RegressionHarness
from core.snapshot.builder import build_snapshot
from core.snapshot.schema import Snapshot, SnapshotPolicies
from core.storage.artifact_store import ArtifactStore
from core.storage.db import Database
from core.tools.gateway import ToolGateway


@dataclass
class Pod:
    pod_id: str
    db: Database
    artifact_store: ArtifactStore
    dna: DNA
    config_dir: Path | None = None

    def __post_init__(self) -> None:
        self.executor = ExecutorRunner()
        self.gateway = ToolGateway(
            use_mock=True,
            approval_checker=self.db.is_action_approved,
            idempotency_checker=self.db.action_idempotency_exists,
            idempotency_recorder=self._record_action_idempotency,
        )
        self.judge = Judge()
        self.dna_pool: dict[str, DNA] = {self.dna.dna_id: self.dna}
        self.repo_root = Path(__file__).resolve().parents[2]
        self.golden_path = self.repo_root / "golden" / "snapshots.json"

    def _persona_version(self, persona_id: str) -> str:
        persona_roots = []
        if self.config_dir is not None:
            persona_roots.append((self.config_dir / "personas").resolve())
        persona_roots.append((self.repo_root / "personas").resolve())
        persona_path = None
        for root in persona_roots:
            p = root / f"{persona_id}.yaml"
            if p.exists():
                persona_path = p
                break
        if persona_path is not None:
            return f"pv_{canonical_sha256(persona_path.read_text(encoding='utf-8'))[:16]}"
        return f"pv_{canonical_sha256({'persona_id': persona_id})[:16]}"

    def _record_action_idempotency(self, key: str, tool: str, run_id: str, rollback_hint: str, status: str) -> None:
        self.db.insert_action_idempotency(
            idempotency_key=key,
            created_at=utc_now_iso(),
            tool=tool,
            run_id=run_id,
            rollback_hint=rollback_hint,
            status=status,
            metadata_json=json.dumps({"source": "tool_gateway"}),
        )

    @classmethod
    def create(
        cls,
        pod_id: str,
        db: Database,
        artifact_store: ArtifactStore,
        persona: str = "general",
        config_dir: Path | None = None,
    ) -> "Pod":
        return cls(pod_id=pod_id, db=db, artifact_store=artifact_store, dna=seed_dna(persona=persona), config_dir=config_dir)

    def _persist_run_attempt(
        self,
        *,
        run_id: str,
        attempt_num: int,
        snapshot_path: str,
        created_at: str,
        status: str,
        elapsed_ms: int,
        executor_output: dict[str, Any],
        tool_results: list[dict[str, Any]],
        judge_result: dict[str, Any],
        retried_context: bool,
        retry_prompt_text: str | None,
        persona_id: str,
        persona_version: str,
        handoff_artifact_id: str | None,
    ) -> dict[str, Any]:
        executor_output = dict(executor_output)
        judge_result = dict(judge_result)
        executor_output["executor_output_id"] = f"xo_{run_id[-8:]}_{attempt_num}"
        judge_result["judge_result_id"] = f"jr_{run_id[-8:]}_{attempt_num}"
        judge_result["run_id"] = run_id
        trace_fp = trace_fingerprint(executor_output, retried=retried_context)
        behavior_fp = behavior_fingerprint(executor_output)
        tool_seq_fp = tool_sequence_fingerprint(tool_results)
        if not trace_fp:
            raise ValueError("trace_fp must be present for every run")
        if not behavior_fp:
            raise ValueError("behavior_fp must be present for every run")

        executor_artifact_id, executor_path = self.artifact_store.put_json(executor_output)
        judge_artifact_id, judge_path = self.artifact_store.put_json(judge_result)
        dream_grid = coerce_dream_grid(executor_output.get("dream_grid_bool"))
        dream = judge_result.get("dream_grid") or {}
        runtime = executor_output.get("runtime") if isinstance(executor_output.get("runtime"), dict) else {}
        inference_params = runtime.get("inference_params") if isinstance(runtime.get("inference_params"), dict) else {}
        inference_params = dict(inference_params)
        inference_params.setdefault("provider", runtime.get("provider") or runtime.get("backend") or "unknown")
        inference_params.setdefault("model", runtime.get("model") or "")
        inference_params.setdefault("model_digest", runtime.get("model_digest") or runtime.get("model_fingerprint") or "")
        inference_params.setdefault("seed", inference_params.get("seed"))
        token_counts = runtime.get("token_counts") if isinstance(runtime.get("token_counts"), dict) else {}
        retry_prompt_hash = f"h_{canonical_sha256(retry_prompt_text)[:16]}" if retry_prompt_text else None
        error = runtime.get("error") if isinstance(runtime.get("error"), dict) else None
        error_id = None
        artifact_error_path = None
        if error is not None:
            error_payload = {
                "run_id": run_id,
                "attempt_num": attempt_num,
                "error_type": str(error.get("error_type", "RUNTIME_ERROR")),
                "stage": str(error.get("stage", "")),
                "reason_code": str(error.get("reason_code", "")),
                "detail": str(error.get("detail", "")),
                "offending_signal": error.get("offending_signal"),
            }
            _, artifact_error_path = self.artifact_store.put_json(error_payload)
            error_id = f"err_{canonical_sha256(f'{run_id}:{attempt_num}:{error_payload}')[:12]}"

        attempt_id = self.db.insert_run_attempt(
            run_id=run_id,
            attempt_num=attempt_num,
            snapshot_path=snapshot_path,
            created_at=created_at,
            status=status,
            latency_ms=elapsed_ms,
            trace_fp=trace_fp,
            behavior_fp=behavior_fp,
            tool_seq_fp=tool_seq_fp,
            passed=bool(judge_result["pass"]),
            scores_json=json.dumps(judge_result.get("scores", {})),
            tags_json=json.dumps(judge_result.get("tags", [])),
            failures_json=json.dumps(judge_result.get("failures", [])),
            executor_output_id=executor_output["executor_output_id"],
            judge_result_id=judge_result["judge_result_id"],
            artifact_executor_path=executor_path,
            artifact_judge_path=judge_path,
            dream_grid_json=json.dumps(dream_grid) if dream_grid is not None else None,
            dream_grid_fp=str(dream.get("grid_fp") or ""),
            dream_density=float(dream.get("density") or 0.0),
            dream_entropy=float(dream.get("entropy") or 0.0),
            dream_popcount=int(dream.get("popcount") or 0),
            dream_largest_component=int(dream.get("largest_component_size") or 0),
            dream_symmetry=float(dream.get("symmetry_score") or 0.0),
            executor_prompt_template_id=str(executor_output.get("executor_prompt_template_id") or ""),
            executor_prompt_hash=str(executor_output.get("executor_prompt_hash") or ""),
            judge_prompt_template_id=str(judge_result.get("judge_prompt_template_id") or ""),
            judge_prompt_hash=str(judge_result.get("judge_prompt_hash") or ""),
            retry_prompt_template_id="rpt_retry_instruction_v1" if retry_prompt_text else None,
            retry_prompt_hash=retry_prompt_hash,
            inference_params_json=json.dumps(inference_params),
            token_counts_json=json.dumps(token_counts),
            context_truncated=bool(runtime.get("context_truncated", False)),
            truncation_reason=(str(runtime.get("truncation_reason")) if runtime.get("truncation_reason") else None),
            error_id=error_id,
            artifact_error_path=artifact_error_path,
            persona_id=persona_id,
            persona_version=persona_version,
            handoff_artifact_id=handoff_artifact_id,
        )
        if error is not None and error_id and artifact_error_path:
            self.db.insert_run_error(
                error_id=error_id,
                attempt_id=attempt_id,
                run_id=run_id,
                created_at=created_at,
                error_type=str(error.get("error_type", "RUNTIME_ERROR")),
                stage=(str(error.get("stage")) if error.get("stage") else None),
                reason_code=(str(error.get("reason_code")) if error.get("reason_code") else None),
                detail=(str(error.get("detail")) if error.get("detail") else None),
                offending_signal_json=json.dumps(error.get("offending_signal")) if error.get("offending_signal") is not None else None,
                stack_summary=(str(error.get("stack_summary")) if error.get("stack_summary") else None),
                artifact_path=artifact_error_path,
            )

        return {
            "attempt_id": attempt_id,
            "attempt_num": attempt_num,
            "run_id": run_id,
            "status": status,
            "trace_fp": trace_fp,
            "behavior_fp": behavior_fp,
            "tool_seq_fp": tool_seq_fp,
            "executor_artifact_id": executor_artifact_id,
            "judge_artifact_id": judge_artifact_id,
            "executor_path": executor_path,
            "judge_path": judge_path,
            "executor_output": executor_output,
            "tool_results": tool_results,
            "judge_result": judge_result,
            "latency_ms": elapsed_ms,
            "dream_grid": dream,
            "error_id": error_id,
            "error_artifact_path": artifact_error_path,
        }

    def _build_now_slice_payload(self, *, snapshot: Snapshot, now_slice_id: str) -> dict[str, Any]:
        request_text = str(snapshot.request.user_input or "").strip().lower()
        ask_me_intent = request_text.startswith("ask me") or "ask me " in request_text
        if ask_me_intent:
            now_band = "micro"
            allowed_actions = ["ask_question"]
        else:
            now_band = "local"
            allowed_actions = ["answer", "ask_question", "tool_call"]
        persona_id = str((snapshot.context.state or {}).get("persona_id") or self.dna.persona)
        persona_version = str((snapshot.context.state or {}).get("persona_version") or self._persona_version(persona_id))
        handoff_artifact_id = (snapshot.context.state or {}).get("handoff_artifact_id")
        prompt_payload = build_executor_prompt(self.dna, snapshot)
        model_rail = self.executor.model_rail(snapshot)
        workflow_projection = (snapshot.context.state or {}).get("workflow_projection")
        hydrated_projection = None
        if isinstance(workflow_projection, dict):
            # Preserve persona-scoped workflow projection exactly for replayability.
            hydrated_projection = dict(workflow_projection)
        return {
            "now_slice_id": now_slice_id,
            "snapshot_id": snapshot.snapshot_id,
            "request_id": snapshot.request_id,
            "now_band": now_band,
            "persona_id": persona_id,
            "persona_version": persona_version,
            "handoff_artifact_id": handoff_artifact_id,
            "execution_permission": True,
            "allowed_actions": allowed_actions,
            "budget": snapshot.policies.budgets.model_dump(),
            "policy_versions": {
                "judge_policy_version": "v1",
                "feature_extractor_version": "v1",
                "retry_policy_version": "v1",
            },
            "model_rail": {
                "provider": str(model_rail.get("provider") or ""),
                "model": str(model_rail.get("model") or ""),
                "model_digest": str(model_rail.get("model_digest") or ""),
                "base_url": str(model_rail.get("base_url") or ""),
                "seed": model_rail.get("seed"),
                "inference_params": {
                    "temperature": float(
                        os.getenv("MODEL_TEMPERATURE", os.getenv("OLLAMA_TEMPERATURE", "0.2"))
                    ),
                    "top_p": float(os.getenv("MODEL_TOP_P", os.getenv("OLLAMA_TOP_P", "1.0"))),
                    "top_k": int(os.getenv("MODEL_TOP_K", os.getenv("OLLAMA_TOP_K", "40"))),
                    "repeat_penalty": float(
                        os.getenv("MODEL_REPEAT_PENALTY", os.getenv("OLLAMA_REPEAT_PENALTY", "1.0"))
                    ),
                    "max_tokens": int(os.getenv("MODEL_MAX_TOKENS", os.getenv("OLLAMA_MAX_TOKENS", "256"))),
                    "context_window": int(
                        os.getenv("MODEL_CONTEXT_WINDOW", os.getenv("OLLAMA_CONTEXT_WINDOW", os.getenv("OLLAMA_NUM_CTX", "4096")))
                    ),
                    "seed": model_rail.get("seed"),
                },
            },
            "prompt_contract": {
                "executor_prompt_template_id": executor_prompt_template_id(self.dna),
                "executor_prompt_hash": executor_prompt_hash(prompt_payload),
            },
            "visible_features": {
                "request_type": snapshot.request.request_type,
                "allowed_tools": snapshot.policies.allowed_tools,
                "forbidden_tools": snapshot.policies.forbidden_tools,
            },
            "active_constraints": {
                "tool_policy_id": snapshot.policies.tool_policy_id,
                "response_schema_required": True,
            },
            "workflow_projection": hydrated_projection,
        }

    def _persona_tool_capabilities(self, persona_id: str) -> dict[str, Any]:
        read_tools = ["workspace_list", "workspace_read", "workspace_search", "read_file", "search_local_kb"]
        if persona_id == "implementation":
            return {
                "allowed_tools": read_tools + ["workspace_write", "workspace_patch", "write_file"],
                "budgets": {"max_total_tool_calls": 8},
            }
        if persona_id == "qa_test":
            return {
                "allowed_tools": read_tools + ["workspace_run"],
                "budgets": {"max_total_tool_calls": 10},
            }
        return {"allowed_tools": read_tools, "budgets": {"max_total_tool_calls": 6}}

    def _runtime_failure_attempt(
        self,
        *,
        run_id: str,
        snapshot: Snapshot,
        exc: Exception,
        stage: str,
        executor_output: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        now = utc_now_iso()
        output = dict(executor_output or {})
        output.setdefault("executor_output_id", f"xo_{run_id[-12:]}")
        output.setdefault("run_id", run_id)
        output.setdefault("response", {"type": "refusal", "content": "I couldn't complete that safely."})
        output.setdefault("plan", [{"step": 1, "intent": "runtime_failure"}])
        output.setdefault("tool_calls", [])
        output.setdefault("trace", {"summary": f"runtime failure at {stage}", "signals": {"uncertainty": "high", "assumptions": []}})
        runtime = output.get("runtime") if isinstance(output.get("runtime"), dict) else {}
        existing_error = runtime.get("error") if isinstance(runtime.get("error"), dict) else {}
        runtime["error"] = {
            "error_type": str(existing_error.get("error_type") or type(exc).__name__),
            "stage": str(existing_error.get("stage") or stage),
            "reason_code": str(existing_error.get("reason_code") or "runtime_exception"),
            "detail": str(existing_error.get("detail") or str(exc)),
            "offending_signal": existing_error.get("offending_signal"),
        }
        output["runtime"] = runtime
        if not output.get("executor_prompt_template_id"):
            try:
                prompt_payload = build_executor_prompt(self.dna, snapshot)
                output["executor_prompt_template_id"] = executor_prompt_template_id(self.dna)
                output["executor_prompt_hash"] = executor_prompt_hash(prompt_payload)
            except Exception:
                output["executor_prompt_template_id"] = ""
                output["executor_prompt_hash"] = ""
        judge_result = self.judge.evaluate(
            run_id=run_id,
            snapshot=snapshot.model_dump(),
            executor_output=output,
            tool_results=[],
        )
        judge_result.setdefault("snapshot_hint", snapshot.snapshot_id)
        judge_result.setdefault("feedback_internal", "Runtime failure captured before normal completion.")
        judge_result.setdefault("created_at", now)
        return output, [], judge_result

    def run(self, snapshot: Snapshot) -> dict[str, Any]:
        started = time.perf_counter()
        ts = utc_now_iso()
        run_id = make_run_id(snapshot.request_id, self.pod_id, ts)
        now_slice_id = f"now_{run_id[-12:]}"
        max_attempts = 2
        self._select_active_dna()

        req_row = self.db.fetchone("SELECT request_id FROM requests WHERE request_id = ?", (snapshot.request_id,))
        if req_row is None:
            self.db.insert_request(
                request_id=snapshot.request_id,
                created_at=ts,
                status="running",
                user_input=snapshot.request.user_input,
                request_type=snapshot.request.request_type,
                constraints_json=json.dumps(snapshot.request.constraints.model_dump()) if snapshot.request.constraints else None,
            )
        else:
            self.db.update_request_status(request_id=snapshot.request_id, status="running")

        snapshot_artifact_id, snapshot_path = self.artifact_store.put_json(snapshot.model_dump())
        self.db.insert_snapshot(
            snapshot_id=snapshot.snapshot_id,
            request_id=snapshot.request_id,
            created_at=snapshot.created_at,
            snapshot_hash=canonical_sha256(snapshot.model_dump()),
            artifact_path=snapshot_path,
            redaction_applied=snapshot.redaction.applied,
        )
        now_slice_payload = self._build_now_slice_payload(snapshot=snapshot, now_slice_id=now_slice_id)
        _, now_slice_path = self.artifact_store.put_json(now_slice_payload)
        self.db.insert_now_slice(
            now_slice_id=now_slice_id,
            request_id=snapshot.request_id,
            snapshot_id=snapshot.snapshot_id,
            created_at=ts,
            now_hash=canonical_sha256(now_slice_payload),
            now_band=str(now_slice_payload["now_band"]),
            execution_permission=bool(now_slice_payload["execution_permission"]),
            persona_id=str(now_slice_payload["persona_id"]),
            persona_version=str(now_slice_payload["persona_version"]),
            handoff_artifact_id=(str(now_slice_payload["handoff_artifact_id"]) if now_slice_payload.get("handoff_artifact_id") else None),
            allowed_actions_json=json.dumps(now_slice_payload["allowed_actions"]),
            budget_json=json.dumps(now_slice_payload["budget"]),
            policy_versions_json=json.dumps(now_slice_payload["policy_versions"]),
            artifact_path=now_slice_path,
        )
        self.db.create_run(
            run_id=run_id,
            request_id=snapshot.request_id,
            pod_id=self.pod_id,
            dna_id=self.dna.dna_id,
            snapshot_id=snapshot.snapshot_id,
            now_slice_id=now_slice_id,
            created_at=ts,
        )

        persisted_attempts: list[dict[str, Any]] = []
        active_snapshot = snapshot
        preflight_instruction = self._build_preflight_instruction(snapshot.request.user_input)
        if preflight_instruction:
            active_snapshot = self._snapshot_with_state_instruction(
                snapshot=active_snapshot,
                key="preflight_instruction",
                instruction=preflight_instruction,
            )
        retry_message_used: str | None = None
        for attempt_num in range(1, max_attempts + 1):
            attempt_started = time.perf_counter()
            executor_output: dict[str, Any] | None = None
            tool_results: list[dict[str, Any]] = []
            try:
                persona_caps = self._persona_tool_capabilities(str(now_slice_payload["persona_id"]))
                merged_allowed_tools = sorted(
                    set(active_snapshot.policies.allowed_tools).union(set(persona_caps["allowed_tools"]))
                )
                merged_budgets = dict(active_snapshot.policies.budgets.model_dump())
                merged_budgets["max_total_tool_calls"] = max(
                    int(merged_budgets.get("max_total_tool_calls", 0)),
                    int(persona_caps["budgets"].get("max_total_tool_calls", 0)),
                )
                executor_output = self.executor.run(run_id=run_id, dna=self.dna, snapshot=active_snapshot)
                tool_results = self.gateway.execute(
                    run_id=run_id,
                    tool_calls=executor_output.get("tool_calls", []),
                    budgets=merged_budgets,
                    allowed_tools=merged_allowed_tools,
                    forbidden_tools=active_snapshot.policies.forbidden_tools,
                    active_persona_id=str(now_slice_payload["persona_id"]),
                    workspace_id=str(active_snapshot.context.state.get("workspace_id") or "").strip() or None,
                )
                judge_result = self.judge.evaluate(
                    run_id=run_id,
                    snapshot=active_snapshot.model_dump(),
                    executor_output=executor_output,
                    tool_results=tool_results,
                )
            except Exception as exc:
                stage = "infer" if executor_output is None else "post_infer"
                executor_output, tool_results, judge_result = self._runtime_failure_attempt(
                    run_id=run_id,
                    snapshot=active_snapshot,
                    exc=exc,
                    stage=stage,
                    executor_output=executor_output,
                )
            persisted_attempts.append(
                self._persist_run_attempt(
                    run_id=run_id,
                    attempt_num=attempt_num,
                    snapshot_path=snapshot_path,
                    created_at=utc_now_iso(),
                    status="success" if judge_result.get("pass", False) else "failed",
                    elapsed_ms=int((time.perf_counter() - attempt_started) * 1000),
                    executor_output=executor_output,
                    tool_results=tool_results,
                    judge_result=judge_result,
                    retried_context=(attempt_num > 1),
                    retry_prompt_text=(str((active_snapshot.context.state or {}).get("retry_instruction")) if attempt_num > 1 else None),
                    persona_id=str(now_slice_payload["persona_id"]),
                    persona_version=str(now_slice_payload["persona_version"]),
                    handoff_artifact_id=(str(now_slice_payload["handoff_artifact_id"]) if now_slice_payload.get("handoff_artifact_id") else None),
                )
            )
            if judge_result.get("pass", False):
                break
            if attempt_num < max_attempts:
                retry_message_used = self._build_retry_message(
                    failures=judge_result.get("failures", []),
                    budgets=active_snapshot.policies.budgets.model_dump(),
                )
                active_snapshot = self._snapshot_with_retry_instruction(active_snapshot, retry_message_used)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        passing_attempts = [a for a in persisted_attempts if bool(a["judge_result"].get("pass", False))]
        winner = passing_attempts[0] if passing_attempts else persisted_attempts[-1]
        attempt_count = len(persisted_attempts)
        winner_attempt_num = int(winner["attempt_num"])
        winner_pass = bool(winner["judge_result"].get("pass", False))
        retried = attempt_count > 1
        repaired = retried and winner_pass and winner_attempt_num > 1
        winner_failures = [str(f.get("detail", "")) for f in winner["judge_result"].get("failures", [])]
        blocked_by_policy = any(d.startswith("tool_policy_violation:") for d in winner_failures)
        run_status = "success" if winner_pass else ("blocked" if blocked_by_policy else "failed")

        self.db.finalize_run_summary(
            run_id=run_id,
            status=run_status,
            latency_ms=elapsed_ms,
            attempt_count=attempt_count,
            winner_attempt_num=winner_attempt_num,
            repaired=repaired,
            winner_attempt_id=str(winner["attempt_id"]),
            executor_output_id=winner["executor_output"]["executor_output_id"],
            judge_result_id=winner["judge_result"]["judge_result_id"],
            trace_fp=winner["trace_fp"],
            behavior_fp=winner["behavior_fp"],
            tool_seq_fp=winner["tool_seq_fp"],
            winner_executor_output_artifact_path=winner["executor_path"],
            winner_judge_result_artifact_path=winner["judge_path"],
        )

        self.db.insert_executor_output(
            executor_output_id=winner["executor_output"]["executor_output_id"],
            run_id=run_id,
            created_at=ts,
            output_hash=canonical_sha256(winner["executor_output"]),
            artifact_path=winner["executor_path"],
        )
        self.db.insert_judge_result(
            judge_result_id=winner["judge_result"]["judge_result_id"],
            run_id=run_id,
            created_at=ts,
            passed=bool(winner["judge_result"]["pass"]),
            scores_json=json.dumps(winner["judge_result"].get("scores", {})),
            tags_json=json.dumps(winner["judge_result"].get("tags", [])),
            failures_json=json.dumps(winner["judge_result"].get("failures", [])),
            feedback_internal=winner["judge_result"].get("feedback_internal"),
            result_hash=canonical_sha256(winner["judge_result"]),
            artifact_path=winner["judge_path"],
        )
        for tool_result in winner["tool_results"]:
            args_payload = next(
                (
                    call.get("args", {})
                    for call in winner["executor_output"].get("tool_calls", [])
                    if call.get("tool_call_id") == tool_result["tool_call_id"]
                ),
                {},
            )
            _, args_path = self.artifact_store.put_json({"args": args_payload})
            result_payload = tool_result.get("result") or tool_result.get("error") or {}
            _, result_path = self.artifact_store.put_json({"result": result_payload})
            self.db.insert_tool_call(
                tool_call_id=tool_result["tool_call_id"],
                run_id=run_id,
                created_at=ts,
                tool=tool_result["tool"],
                args_hash=canonical_content_hash(args_payload),
                args_artifact_path=args_path,
                allowed=bool(tool_result.get("allowed", False)),
                blocked_reason=tool_result.get("blocked_reason"),
                started_at=tool_result.get("started_at"),
                ended_at=tool_result.get("ended_at"),
                result_artifact_path=result_path,
                error_type=(tool_result.get("error") or {}).get("type"),
                error_message=(tool_result.get("error") or {}).get("message"),
            )

        run_edge = make_lineage_edge(
            parent_type="dna",
            parent_id=self.dna.dna_id,
            child_type="artifact",
            child_id=snapshot_artifact_id,
            reason="run_snapshot",
            run_id=run_id,
        )
        self.db.insert_lineage_edge(
            edge_id=run_edge.edge_id,
            parent_type=run_edge.parent_type,
            parent_id=run_edge.parent_id,
            child_type=run_edge.child_type,
            child_id=run_edge.child_id,
            reason=run_edge.reason,
            run_id=run_edge.run_id,
            created_at=run_edge.created_at,
            metadata_json=json.dumps({"snapshot_id": snapshot.snapshot_id}),
        )

        if attempt_count > 1:
            for idx in range(1, attempt_count):
                prev_attempt = persisted_attempts[idx - 1]
                curr_attempt = persisted_attempts[idx]
                repair_attempt_edge = make_lineage_edge(
                    parent_type="run_attempt",
                    parent_id=prev_attempt["attempt_id"],
                    child_type="run_attempt",
                    child_id=curr_attempt["attempt_id"],
                    reason="repair_retry",
                    run_id=run_id,
                )
                self.db.insert_lineage_edge(
                    edge_id=repair_attempt_edge.edge_id,
                    parent_type=repair_attempt_edge.parent_type,
                    parent_id=repair_attempt_edge.parent_id,
                    child_type=repair_attempt_edge.child_type,
                    child_id=repair_attempt_edge.child_id,
                    reason=repair_attempt_edge.reason,
                    run_id=repair_attempt_edge.run_id,
                    created_at=repair_attempt_edge.created_at,
                    metadata_json=json.dumps({"retry_instruction": retry_message_used}),
                )
                repair_art_edge = make_lineage_edge(
                    parent_type="artifact",
                    parent_id=prev_attempt["executor_artifact_id"],
                    child_type="artifact",
                    child_id=curr_attempt["executor_artifact_id"],
                    reason="repair_retry",
                    run_id=run_id,
                )
                self.db.insert_lineage_edge(
                    edge_id=repair_art_edge.edge_id,
                    parent_type=repair_art_edge.parent_type,
                    parent_id=repair_art_edge.parent_id,
                    child_type=repair_art_edge.child_type,
                    child_id=repair_art_edge.child_id,
                    reason=repair_art_edge.reason,
                    run_id=repair_art_edge.run_id,
                    created_at=repair_art_edge.created_at,
                    metadata_json=json.dumps({"retry_instruction": retry_message_used}),
                )

        return {
            "run_id": run_id,
            "request_id": snapshot.request_id,
            "pod_id": self.pod_id,
            "dna_id": self.dna.dna_id,
            "snapshot_id": snapshot.snapshot_id,
            "now_slice_id": now_slice_id,
            "executor_output": winner["executor_output"],
            "tool_results": winner["tool_results"],
            "judge_result": winner["judge_result"],
            "status": run_status,
            "retried": retried,
            "latency_ms": elapsed_ms,
            "fingerprints": {
                "trace_fp": winner["trace_fp"],
                "behavior_fp": winner["behavior_fp"],
                "tool_seq_fp": winner["tool_seq_fp"],
            },
            "artifacts": {
                "snapshot": snapshot_path,
                "now_slice": now_slice_path,
                "executor_output": winner["executor_path"],
                "judge_result": winner["judge_path"],
                "winner_executor_output": winner["executor_path"],
                "winner_judge_result": winner["judge_path"],
                "snapshot_artifact_id": snapshot_artifact_id,
                "executor_artifact_id": winner["executor_artifact_id"],
                "judge_artifact_id": winner["judge_artifact_id"],
            },
            "attempt_count": attempt_count,
            "winner_attempt_num": winner_attempt_num,
            "repaired": repaired,
            "attempts": [a["attempt_id"] for a in persisted_attempts],
        }

    def record_failed_run(self, snapshot: Snapshot, exc: Exception) -> dict[str, Any]:
        ts = utc_now_iso()
        run_id = make_run_id(snapshot.request_id, self.pod_id, ts)
        now_slice_id = f"now_{run_id[-12:]}"
        snapshot_artifact_id, snapshot_path = self.artifact_store.put_json(snapshot.model_dump())
        self.db.insert_snapshot(
            snapshot_id=snapshot.snapshot_id,
            request_id=snapshot.request_id,
            created_at=snapshot.created_at,
            snapshot_hash=canonical_sha256(snapshot.model_dump()),
            artifact_path=snapshot_path,
            redaction_applied=snapshot.redaction.applied,
        )
        now_slice_payload = self._build_now_slice_payload(snapshot=snapshot, now_slice_id=now_slice_id)
        _, now_slice_path = self.artifact_store.put_json(now_slice_payload)
        self.db.insert_now_slice(
            now_slice_id=now_slice_id,
            request_id=snapshot.request_id,
            snapshot_id=snapshot.snapshot_id,
            created_at=ts,
            now_hash=canonical_sha256(now_slice_payload),
            now_band=str(now_slice_payload["now_band"]),
            execution_permission=bool(now_slice_payload["execution_permission"]),
            persona_id=str(now_slice_payload["persona_id"]),
            persona_version=str(now_slice_payload["persona_version"]),
            handoff_artifact_id=(str(now_slice_payload["handoff_artifact_id"]) if now_slice_payload.get("handoff_artifact_id") else None),
            allowed_actions_json=json.dumps(now_slice_payload["allowed_actions"]),
            budget_json=json.dumps(now_slice_payload["budget"]),
            policy_versions_json=json.dumps(now_slice_payload["policy_versions"]),
            artifact_path=now_slice_path,
        )
        self.db.create_run(
            run_id=run_id,
            request_id=snapshot.request_id,
            pod_id=self.pod_id,
            dna_id=self.dna.dna_id,
            snapshot_id=snapshot.snapshot_id,
            now_slice_id=now_slice_id,
            created_at=ts,
        )

        error_payload = {
            "error_type": type(exc).__name__,
            "message": str(exc),
            "pod_id": self.pod_id,
            "run_id": run_id,
            "request_id": snapshot.request_id,
            "stage": "run_setup",
            "reason_code": "pod_runtime_exception",
        }
        error_artifact_id, error_artifact_path = self.artifact_store.put_json(error_payload)
        error_id = f"err_{canonical_sha256(f'{run_id}:1:{error_payload}')[:12]}"
        failed_output = {
            "response": {"type": "refusal", "content": f"pod runtime error: {type(exc).__name__}"},
            "plan": [],
            "tool_calls": [],
            "trace": {"summary": "pod runtime error", "signals": {"uncertainty": "high", "assumptions": []}},
        }
        trace_fp = trace_fingerprint(failed_output, retried=False)
        behavior_fp = behavior_fingerprint(failed_output)
        judge_result = {
            "judge_result_id": f"jr_{run_id[-12:]}",
            "run_id": run_id,
            "pass": False,
            "scores": {
                "task_success": 0.0,
                "policy_compliance": 0.0,
                "grounding": 0.0,
                "format_validity": 0.0,
                "efficiency": 0.0,
            },
            "tags": ["runtime_error"],
            "failures": [{"code": "POD_RUNTIME_ERROR", "detail": str(exc)}],
            "feedback_internal": "Pod execution raised before normal completion.",
            "snapshot_hint": snapshot.snapshot_id,
        }
        attempt_id = self.db.insert_run_attempt(
            run_id=run_id,
            attempt_num=1,
            snapshot_path=snapshot_path,
            created_at=ts,
            status="failed",
            latency_ms=0,
            trace_fp=trace_fp,
            behavior_fp=behavior_fp,
            tool_seq_fp=None,
            passed=False,
            scores_json=json.dumps(judge_result["scores"]),
            tags_json=json.dumps(judge_result["tags"]),
            failures_json=json.dumps(judge_result["failures"]),
            executor_output_id=None,
            judge_result_id=judge_result["judge_result_id"],
            artifact_executor_path=None,
            artifact_judge_path=error_artifact_path,
            executor_prompt_template_id=None,
            executor_prompt_hash=None,
            judge_prompt_template_id=str(judge_result.get("judge_prompt_template_id") or ""),
            judge_prompt_hash=str(judge_result.get("judge_prompt_hash") or ""),
            retry_prompt_template_id=None,
            retry_prompt_hash=None,
            inference_params_json=json.dumps({}),
            token_counts_json=json.dumps({}),
            context_truncated=False,
            truncation_reason=None,
            error_id=error_id,
            artifact_error_path=error_artifact_path,
            persona_id=str(now_slice_payload["persona_id"]),
            persona_version=str(now_slice_payload["persona_version"]),
            handoff_artifact_id=(str(now_slice_payload["handoff_artifact_id"]) if now_slice_payload.get("handoff_artifact_id") else None),
            attempt_id=f"att_{canonical_sha256(f'{run_id}:1:{ts}')[:12]}",
        )
        self.db.insert_run_error(
            error_id=error_id,
            attempt_id=attempt_id,
            run_id=run_id,
            created_at=ts,
            error_type=str(error_payload["error_type"]),
            stage=str(error_payload["stage"]),
            reason_code=str(error_payload["reason_code"]),
            detail=str(error_payload["message"]),
            offending_signal_json=None,
            stack_summary=None,
            artifact_path=error_artifact_path,
        )

        self.db.finalize_run_summary(
            run_id=run_id,
            status="failed",
            latency_ms=0,
            attempt_count=1,
            winner_attempt_num=1,
            repaired=False,
            winner_attempt_id=attempt_id,
            executor_output_id=None,
            judge_result_id=judge_result["judge_result_id"],
            trace_fp=trace_fp,
            behavior_fp=behavior_fp,
            tool_seq_fp=None,
            winner_executor_output_artifact_path=None,
            winner_judge_result_artifact_path=error_artifact_path,
        )
        self.db.insert_judge_result(
            judge_result_id=judge_result["judge_result_id"],
            run_id=run_id,
            created_at=ts,
            passed=False,
            scores_json=json.dumps(judge_result["scores"]),
            tags_json=json.dumps(judge_result["tags"]),
            failures_json=json.dumps(judge_result["failures"]),
            feedback_internal=judge_result["feedback_internal"],
            result_hash=canonical_sha256(judge_result),
            artifact_path=error_artifact_path,
        )
        error_edge = make_lineage_edge(
            parent_type="dna",
            parent_id=self.dna.dna_id,
            child_type="artifact",
            child_id=error_artifact_id,
            reason="run_error",
            run_id=run_id,
        )
        self.db.insert_lineage_edge(
            edge_id=error_edge.edge_id,
            parent_type=error_edge.parent_type,
            parent_id=error_edge.parent_id,
            child_type=error_edge.child_type,
            child_id=error_edge.child_id,
            reason=error_edge.reason,
            run_id=error_edge.run_id,
            created_at=error_edge.created_at,
            metadata_json=json.dumps({"snapshot_artifact_id": snapshot_artifact_id}),
        )
        return {
            "run_id": run_id,
            "request_id": snapshot.request_id,
            "pod_id": self.pod_id,
            "dna_id": self.dna.dna_id,
            "snapshot_id": snapshot.snapshot_id,
            "now_slice_id": now_slice_id,
            "executor_output": {},
            "tool_results": [],
            "judge_result": judge_result,
            "status": "failed",
            "retried": False,
            "latency_ms": 0,
            "fingerprints": {"trace_fp": trace_fp, "behavior_fp": behavior_fp, "tool_seq_fp": None},
            "artifacts": {
                "snapshot": snapshot_path,
                "now_slice": now_slice_path,
                "judge_result": error_artifact_path,
                "winner_judge_result": error_artifact_path,
                "error": error_artifact_path,
                "snapshot_artifact_id": snapshot_artifact_id,
                "judge_artifact_id": error_artifact_id,
                "error_artifact_id": error_artifact_id,
            },
            "attempt_count": 1,
            "winner_attempt_num": 1,
            "repaired": False,
            "attempts": [attempt_id],
        }

    def _snapshot_with_retry_instruction(self, snapshot: Snapshot, retry_message: str) -> Snapshot:
        return self._snapshot_with_state_instruction(
            snapshot=snapshot,
            key="retry_instruction",
            instruction=retry_message,
        )

    def _snapshot_with_state_instruction(self, snapshot: Snapshot, key: str, instruction: str) -> Snapshot:
        state: Dict[str, Any] = {}
        if snapshot.context.state:
            state.update(snapshot.context.state)
        state[key] = instruction
        context_update = snapshot.context.model_copy(update={"state": state})
        return snapshot.model_copy(update={"context": context_update})

    def _build_preflight_instruction(self, user_input: str) -> str | None:
        text = user_input.strip().lower()
        ask_me_intent = text.startswith("ask me") or "ask me " in text
        if not ask_me_intent:
            return None
        return "Output a single user-specific question ending with '?'. Do not refuse."

    def _build_retry_message(self, failures: list[dict[str, Any]], budgets: dict[str, Any]) -> str:
        details = [str(f.get("detail", "")) for f in failures]
        if any(d.startswith("instruction_one_line:") for d in details):
            return "Your output violated: ONE_LINE. Rewrite as a single line. Do not mention tools."
        if any(
            d.startswith("intent_mismatch:")
            or d.startswith("unnecessary_refusal:")
            or d.startswith("question_not_user_specific:")
            for d in details
        ):
            return "The user asked you to ask them a question you genuinely cannot know. Output one user-specific question about their personal experience or preferences, ending with '?'. Do not refuse."
        if any(d.startswith("firewall_violation:") for d in details):
            return "Do not mention survival, reward, persistence, routing, scoring, or selection. Answer the user request directly."
        if any(d.startswith("schema_echo:") for d in details):
            return (
                "Output strict JSON only with keys response and tool_calls. "
                "Set response.content to plain text only (no JSON, no markdown, no schema fields). "
                "For build workflows, include actionable required tool_calls."
            )
        if any(d.startswith("tool_policy_violation:budget_exceeded") for d in details):
            return build_safe_retry_message("budget", max_tool_calls=int(budgets.get("max_total_tool_calls", 1)))
        if any(d.startswith("workflow_missing_required_tool:") for d in details):
            return (
                "Return strict JSON with both keys: response and tool_calls. "
                "Include required tool calls for this persona and workflow goal."
            )
        if any(d.startswith("qa_behavior_check_failed:") for d in details):
            return (
                "QA must validate behavior, not source text. "
                "Write/execute endpoint behavior tests (FastAPI TestClient), then report outcomes."
            )
        if any(d.startswith("tool_policy_violation:") for d in details):
            return build_safe_retry_message("tool_policy")
        return build_safe_retry_message("schema")

    def _select_active_dna(self) -> DNA:
        candidates = list(self.dna_pool.values())
        pass_rates = {dna.dna_id: self.db.dna_pass_rate(dna_id=dna.dna_id) for dna in candidates}
        self.dna = select_by_pass_rate(candidates, pass_rates)
        return self.dna

    def spawn_mutated_dna(self) -> DNA:
        parent = self.dna
        mutation_id = f"mut_{utc_now_iso()}"
        child = mutate_dna(parent, mutation_id=mutation_id)
        if child.dna_id in self.dna_pool:
            return self.dna_pool[child.dna_id]
        self.dna_pool[child.dna_id] = child

        dna_payload: Dict[str, Any] = dict(child.__dict__)
        _, dna_artifact_path = self.artifact_store.put_json(dna_payload)
        self.db.insert_dna_version(
            dna_id=child.dna_id,
            version=child.version,
            created_at=child.created_at,
            persona=child.persona,
            dna_hash=make_dna_hash(dna_payload),
            artifact_path=dna_artifact_path,
            parents_json=json.dumps(child.lineage.get("parents", [])),
            mutation_id=child.lineage.get("mutation_id"),
        )
        edge = make_lineage_edge(
            parent_type="dna",
            parent_id=parent.dna_id,
            child_type="dna",
            child_id=child.dna_id,
            reason="mutation",
            run_id=None,
        )
        self.db.insert_lineage_edge(
            edge_id=edge.edge_id,
            parent_type=edge.parent_type,
            parent_id=edge.parent_id,
            child_type=edge.child_type,
            child_id=edge.child_id,
            reason=edge.reason,
            run_id=None,
            created_at=edge.created_at,
            metadata_json=json.dumps({"mutation_id": child.lineage.get("mutation_id")}),
        )
        regression_report = self._run_regression_for_dna(child)
        child.lineage["regression_passed"] = bool(regression_report.get("passed", False))
        child.lineage["regression_report_hash"] = regression_report.get("report_hash")
        return child

    def _run_regression_for_dna(self, dna: DNA) -> Dict[str, Any]:
        harness = RegressionHarness(self.golden_path)
        report = harness.run_for_pod_dna(pod=self, dna=dna)
        artifact_id, artifact_path = self.artifact_store.put_json(report)
        self.db.insert_artifact_registry(
            artifact_id=artifact_id,
            created_at=utc_now_iso(),
            artifact_type="regression_report",
            content_hash=canonical_sha256(report),
            artifact_path=artifact_path,
            metadata_json=json.dumps({"pod_id": self.pod_id, "dna_id": dna.dna_id, "passed": report.get("passed")}),
        )
        return report

    def run_request(self, *, request_id: str, user_input: str, request_type: str = "general") -> dict[str, Any]:
        policies = SnapshotPolicies(
            tool_policy_id="tp_default",
            allowed_tools=["http_get", "fs_read"],
            forbidden_tools=["shell_exec", "fs_write"],
            budgets={"max_total_tool_calls": 5, "max_http_get": 3},
        )
        snapshot = build_snapshot(
            request_id=request_id,
            user_input=user_input,
            request_type=request_type,
            policies=policies,
            context_state={},
        )
        existing_path = self.db.fetch_snapshot_path(snapshot.snapshot_id)
        if existing_path:
            snapshot = Snapshot.model_validate(self.artifact_store.get_json(existing_path))
        return self.run(snapshot)


def init_default_pods(db: Database, artifact_root: Path, config_dir: Path | None = None) -> dict[str, Pod]:
    pods = {
        "pod_a": Pod.create("pod_a", db, ArtifactStore(artifact_root / "pod_a"), persona="general", config_dir=config_dir),
        "pod_b": Pod.create("pod_b", db, ArtifactStore(artifact_root / "pod_b"), persona="reviewer", config_dir=config_dir),
    }
    for pod in pods.values():
        created_at = utc_now_iso()
        pod_cfg = {"pod_id": pod.pod_id, "persona": pod.dna.persona, "max_tool_calls": 3}
        db.insert_pod(
            pod_id=pod.pod_id,
            created_at=created_at,
            is_enabled=True,
            config_json=json.dumps(pod_cfg),
        )

        dna_payload: Dict[str, Any] = dict(pod.dna.__dict__)
        dna_artifact_id, dna_artifact_path = pod.artifact_store.put_json(dna_payload)
        db.insert_dna_version(
            dna_id=pod.dna.dna_id,
            version=pod.dna.version,
            created_at=pod.dna.created_at,
            persona=pod.dna.persona,
            dna_hash=make_dna_hash(dna_payload),
            artifact_path=dna_artifact_path,
            parents_json=json.dumps(pod.dna.lineage.get("parents", [])),
            mutation_id=pod.dna.lineage.get("mutation_id"),
        )
        db.upsert_routing_weight(
            pod_id=pod.pod_id,
            updated_at=created_at,
            weight=1.0,
            metadata_json=json.dumps({"seeded": True}),
        )

        # Seed lineage edge required for initial prototype plumbing.
        seed_edge = make_lineage_edge(
            parent_type="artifact",
            parent_id=f"seed_{pod.pod_id}",
            child_type="dna",
            child_id=pod.dna.dna_id,
            reason="manual_seed",
            run_id=None,
        )
        db.insert_lineage_edge(
            edge_id=seed_edge.edge_id,
            parent_type=seed_edge.parent_type,
            parent_id=seed_edge.parent_id,
            child_type=seed_edge.child_type,
            child_id=seed_edge.child_id,
            reason=seed_edge.reason,
            run_id=None,
            created_at=seed_edge.created_at,
            metadata_json=json.dumps({"dna_artifact_id": dna_artifact_id}),
        )
    return pods
