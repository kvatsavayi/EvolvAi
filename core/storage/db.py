from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class Database:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def migrate(self, migration_sql_path: Path) -> None:
        sql = migration_sql_path.read_text(encoding="utf-8")
        with self._lock:
            self.conn.executescript(sql)
            cols = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(external_signals)").fetchall()}
            if "request_type" not in cols:
                self.conn.execute("ALTER TABLE external_signals ADD COLUMN request_type TEXT")
            run_cols = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "behavior_fp" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN behavior_fp TEXT NOT NULL DEFAULT ''")
                self.conn.execute("UPDATE runs SET behavior_fp = trace_fp WHERE behavior_fp = ''")
            if "attempt_count" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1")
            if "winner_attempt_num" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN winner_attempt_num INTEGER NOT NULL DEFAULT 1")
            if "winner_attempt_id" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN winner_attempt_id TEXT")
            if "repaired" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN repaired INTEGER NOT NULL DEFAULT 0")
            if "winner_executor_output_artifact_path" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN winner_executor_output_artifact_path TEXT")
            if "winner_judge_result_artifact_path" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN winner_judge_result_artifact_path TEXT")
            if "now_slice_id" not in run_cols:
                self.conn.execute("ALTER TABLE runs ADD COLUMN now_slice_id TEXT")
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS now_slices (
                  now_slice_id TEXT PRIMARY KEY,
                  request_id TEXT NOT NULL,
                  snapshot_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  now_hash TEXT NOT NULL,
                  now_band TEXT NOT NULL DEFAULT 'local',
                  persona_id TEXT NOT NULL DEFAULT 'general',
                  persona_version TEXT NOT NULL DEFAULT 'pv_unknown',
                  handoff_artifact_id TEXT,
                  execution_permission INTEGER NOT NULL DEFAULT 1,
                  allowed_actions_json TEXT NOT NULL,
                  budget_json TEXT NOT NULL,
                  policy_versions_json TEXT NOT NULL,
                  artifact_path TEXT NOT NULL,
                  FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE CASCADE,
                  FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) ON DELETE RESTRICT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_attempts (
                  attempt_id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  attempt_num INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  status TEXT NOT NULL,
                  latency_ms INTEGER NOT NULL DEFAULT 0,
                  executor_output_id TEXT,
                  judge_result_id TEXT,
                  trace_fp TEXT NOT NULL,
                  behavior_fp TEXT NOT NULL,
                  tool_seq_fp TEXT,
                  artifact_snapshot_path TEXT NOT NULL,
                  artifact_executor_path TEXT,
                  artifact_judge_path TEXT,
                  pass INTEGER NOT NULL DEFAULT 0,
                  dream_grid_json TEXT,
                  dream_grid_fp TEXT,
                  dream_density REAL NOT NULL DEFAULT 0.0,
                  dream_entropy REAL NOT NULL DEFAULT 0.0,
                  dream_popcount INTEGER NOT NULL DEFAULT 0,
                  dream_largest_component INTEGER NOT NULL DEFAULT 0,
                  dream_symmetry REAL NOT NULL DEFAULT 0.0,
                  executor_prompt_template_id TEXT,
                  executor_prompt_hash TEXT,
                  judge_prompt_template_id TEXT,
                  judge_prompt_hash TEXT,
                  retry_prompt_template_id TEXT,
                  retry_prompt_hash TEXT,
                  inference_params_json TEXT,
                  token_counts_json TEXT,
                  context_truncated INTEGER NOT NULL DEFAULT 0,
                  truncation_reason TEXT,
                  error_id TEXT,
                  artifact_error_path TEXT,
                  persona_id TEXT NOT NULL DEFAULT 'general',
                  persona_version TEXT NOT NULL DEFAULT 'pv_unknown',
                  handoff_artifact_id TEXT,
                  workspace_ops_count INTEGER NOT NULL DEFAULT 0,
                  bytes_written INTEGER NOT NULL DEFAULT 0,
                  knowledge_reads_count INTEGER NOT NULL DEFAULT 0,
                  knowledge_commits_count INTEGER NOT NULL DEFAULT 0,
                  knowledge_commit_attempts INTEGER NOT NULL DEFAULT 0,
                  knowledge_commit_pass_rate REAL NOT NULL DEFAULT 0.0,
                  source_artifact_ids_json TEXT,
                  scores_json TEXT,
                  tags_json TEXT,
                  failures_json TEXT,
                  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_errors (
                  error_id TEXT PRIMARY KEY,
                  attempt_id TEXT NOT NULL UNIQUE,
                  run_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  error_type TEXT NOT NULL,
                  stage TEXT,
                  reason_code TEXT,
                  detail TEXT,
                  offending_signal_json TEXT,
                  stack_summary TEXT,
                  artifact_path TEXT NOT NULL,
                  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
                  FOREIGN KEY (attempt_id) REFERENCES run_attempts(attempt_id) ON DELETE CASCADE
                )
                """
            )
            attempt_cols = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(run_attempts)").fetchall()}
            if "scores_json" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN scores_json TEXT")
            if "tags_json" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN tags_json TEXT")
            if "dream_grid_json" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN dream_grid_json TEXT")
            if "dream_grid_fp" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN dream_grid_fp TEXT")
            if "dream_density" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN dream_density REAL NOT NULL DEFAULT 0.0")
            if "dream_entropy" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN dream_entropy REAL NOT NULL DEFAULT 0.0")
            if "dream_popcount" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN dream_popcount INTEGER NOT NULL DEFAULT 0")
            if "dream_largest_component" not in attempt_cols:
                self.conn.execute(
                    "ALTER TABLE run_attempts ADD COLUMN dream_largest_component INTEGER NOT NULL DEFAULT 0"
                )
            if "dream_symmetry" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN dream_symmetry REAL NOT NULL DEFAULT 0.0")
            if "executor_prompt_template_id" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN executor_prompt_template_id TEXT")
            if "executor_prompt_hash" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN executor_prompt_hash TEXT")
            if "judge_prompt_template_id" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN judge_prompt_template_id TEXT")
            if "judge_prompt_hash" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN judge_prompt_hash TEXT")
            if "retry_prompt_template_id" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN retry_prompt_template_id TEXT")
            if "retry_prompt_hash" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN retry_prompt_hash TEXT")
            if "inference_params_json" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN inference_params_json TEXT")
            if "token_counts_json" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN token_counts_json TEXT")
            if "context_truncated" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN context_truncated INTEGER NOT NULL DEFAULT 0")
            if "truncation_reason" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN truncation_reason TEXT")
            if "error_id" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN error_id TEXT")
            if "artifact_error_path" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN artifact_error_path TEXT")
            if "workspace_ops_count" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN workspace_ops_count INTEGER NOT NULL DEFAULT 0")
            if "bytes_written" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN bytes_written INTEGER NOT NULL DEFAULT 0")
            if "knowledge_reads_count" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN knowledge_reads_count INTEGER NOT NULL DEFAULT 0")
            if "knowledge_commits_count" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN knowledge_commits_count INTEGER NOT NULL DEFAULT 0")
            if "knowledge_commit_attempts" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN knowledge_commit_attempts INTEGER NOT NULL DEFAULT 0")
            if "knowledge_commit_pass_rate" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN knowledge_commit_pass_rate REAL NOT NULL DEFAULT 0.0")
            if "source_artifact_ids_json" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN source_artifact_ids_json TEXT")
            if "persona_id" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN persona_id TEXT NOT NULL DEFAULT 'general'")
            if "persona_version" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN persona_version TEXT NOT NULL DEFAULT 'pv_unknown'")
            if "handoff_artifact_id" not in attempt_cols:
                self.conn.execute("ALTER TABLE run_attempts ADD COLUMN handoff_artifact_id TEXT")
            now_cols = {str(r["name"]) for r in self.conn.execute("PRAGMA table_info(now_slices)").fetchall()}
            if "persona_id" not in now_cols:
                self.conn.execute("ALTER TABLE now_slices ADD COLUMN persona_id TEXT NOT NULL DEFAULT 'general'")
            if "persona_version" not in now_cols:
                self.conn.execute("ALTER TABLE now_slices ADD COLUMN persona_version TEXT NOT NULL DEFAULT 'pv_unknown'")
            if "handoff_artifact_id" not in now_cols:
                self.conn.execute("ALTER TABLE now_slices ADD COLUMN handoff_artifact_id TEXT")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_external_signals_type ON external_signals(request_type, pod_id, signal_type)"
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_behavior_fp ON runs(behavior_fp)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_repaired ON runs(repaired)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_now_slice_id ON runs(now_slice_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_now_slices_request_id ON now_slices(request_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_now_slices_snapshot_id ON now_slices(snapshot_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_now_slices_hash ON now_slices(now_hash)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_run_attempts_run ON run_attempts(run_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_run_attempts_behavior_fp ON run_attempts(behavior_fp)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_run_errors_run_id ON run_errors(run_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_run_errors_type ON run_errors(error_type)")
            legacy_rows = self.conn.execute(
                """
                SELECT
                  r.run_id,
                  r.status,
                  r.created_at,
                  r.latency_ms,
                  r.trace_fp,
                  r.behavior_fp,
                  r.tool_seq_fp,
                  r.executor_output_id,
                  r.judge_result_id,
                  r.winner_attempt_num,
                  r.winner_attempt_id,
                  r.attempt_count,
                  s.artifact_path AS snapshot_artifact_path,
                  COALESCE(r.winner_executor_output_artifact_path, eo.artifact_path) AS executor_artifact_path,
                  COALESCE(r.winner_judge_result_artifact_path, jr.artifact_path) AS judge_artifact_path,
                  COALESCE(jr.pass, 0) AS pass,
                  jr.scores_json,
                  jr.tags_json,
                  jr.failures_json
                FROM runs r
                LEFT JOIN snapshots s ON s.snapshot_id = r.snapshot_id
                LEFT JOIN executor_outputs eo ON eo.executor_output_id = r.executor_output_id
                LEFT JOIN judge_results jr ON jr.judge_result_id = r.judge_result_id
                WHERE NOT EXISTS (SELECT 1 FROM run_attempts ra WHERE ra.run_id = r.run_id)
                """
            ).fetchall()
            for row in legacy_rows:
                run_id = str(row["run_id"])
                attempt_num = int(row["winner_attempt_num"] or 1)
                if attempt_num < 1:
                    attempt_num = 1
                attempt_id = str(row["winner_attempt_id"] or "").strip() or f"att_legacy_{run_id[-12:]}"
                pass_flag = int(row["pass"] or 0)
                status = str(row["status"] or "")
                if status not in {"success", "failed", "blocked"}:
                    status = "success" if pass_flag else "failed"
                self.conn.execute(
                    """
                    INSERT OR REPLACE INTO run_attempts (
                      attempt_id, run_id, attempt_num, created_at, status, latency_ms,
                      executor_output_id, judge_result_id, trace_fp, behavior_fp, tool_seq_fp,
                      artifact_snapshot_path, artifact_executor_path, artifact_judge_path,
                      pass, dream_grid_json, dream_grid_fp, dream_density, dream_entropy,
                      dream_popcount, dream_largest_component, dream_symmetry,
                      executor_prompt_template_id, executor_prompt_hash, judge_prompt_template_id, judge_prompt_hash,
                      retry_prompt_template_id, retry_prompt_hash, inference_params_json, token_counts_json,
                      context_truncated, truncation_reason,
                      scores_json, tags_json, failures_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        run_id,
                        attempt_num,
                        str(row["created_at"]),
                        status,
                        int(row["latency_ms"] or 0),
                        row["executor_output_id"],
                        row["judge_result_id"],
                        str(row["trace_fp"] or "fp_legacy_trace"),
                        str(row["behavior_fp"] or "fp_legacy_behavior"),
                        row["tool_seq_fp"],
                        str(row["snapshot_artifact_path"] or ""),
                        row["executor_artifact_path"],
                        row["judge_artifact_path"],
                        pass_flag,
                        None,
                        None,
                        0.0,
                        0.0,
                        0,
                        0,
                        0.0,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        0,
                        None,
                        row["scores_json"],
                        row["tags_json"],
                        row["failures_json"],
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE runs
                    SET winner_attempt_id = ?,
                        winner_attempt_num = CASE WHEN winner_attempt_num < 1 THEN 1 ELSE winner_attempt_num END,
                        attempt_count = CASE WHEN attempt_count < 1 THEN 1 ELSE attempt_count END,
                        winner_executor_output_artifact_path = COALESCE(winner_executor_output_artifact_path, ?),
                        winner_judge_result_artifact_path = COALESCE(winner_judge_result_artifact_path, ?)
                    WHERE run_id = ?
                    """,
                    (attempt_id, row["executor_artifact_path"], row["judge_artifact_path"], run_id),
                )
            self.conn.execute(
                """
                UPDATE runs
                SET repaired = CASE
                    WHEN attempt_count > 1
                     AND winner_attempt_num > 1
                     AND COALESCE((SELECT pass FROM judge_results jr WHERE jr.run_id = runs.run_id), 0) = 1
                    THEN 1 ELSE 0 END
                """
            )
            self.conn.commit()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self.conn.execute(query, params)
            self.conn.commit()

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(query, params).fetchall())

    def insert_request(
        self,
        *,
        request_id: str,
        created_at: str,
        status: str,
        user_input: str,
        request_type: str,
        constraints_json: Optional[str] = None,
    ) -> None:
        self.execute(
            """
            INSERT INTO requests (request_id, created_at, status, user_input, request_type, constraints_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
              status = excluded.status,
              user_input = excluded.user_input,
              request_type = excluded.request_type,
              constraints_json = excluded.constraints_json
            """,
            (request_id, created_at, status, user_input, request_type, constraints_json),
        )

    def update_request_status(self, *, request_id: str, status: str) -> None:
        self.execute("UPDATE requests SET status = ? WHERE request_id = ?", (status, request_id))

    def insert_pod(self, *, pod_id: str, created_at: str, is_enabled: bool, config_json: str) -> None:
        self.execute(
            """
            INSERT INTO pods (pod_id, created_at, is_enabled, config_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pod_id) DO UPDATE SET
              is_enabled = excluded.is_enabled,
              config_json = excluded.config_json
            """,
            (pod_id, created_at, int(is_enabled), config_json),
        )

    def insert_dna_version(
        self,
        *,
        dna_id: str,
        version: int,
        created_at: str,
        persona: str,
        dna_hash: str,
        artifact_path: str,
        parents_json: Optional[str],
        mutation_id: Optional[str],
    ) -> None:
        self.execute(
            """
            INSERT INTO dna_versions (dna_id, version, created_at, persona, dna_hash, artifact_path, parents_json, mutation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dna_id) DO UPDATE SET
              version = excluded.version,
              persona = excluded.persona,
              dna_hash = excluded.dna_hash,
              artifact_path = excluded.artifact_path,
              parents_json = excluded.parents_json,
              mutation_id = excluded.mutation_id
            """,
            (dna_id, version, created_at, persona, dna_hash, artifact_path, parents_json, mutation_id),
        )

    def insert_snapshot(
        self,
        *,
        snapshot_id: str,
        request_id: str,
        created_at: str,
        snapshot_hash: str,
        artifact_path: str,
        redaction_applied: bool,
    ) -> None:
        existing = self.fetchone(
            "SELECT snapshot_id, snapshot_hash FROM snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        if existing is not None:
            # Snapshot ids are request-scoped in v1; keep first persisted artifact as source of truth.
            return
        self.execute(
            "INSERT INTO snapshots (snapshot_id, request_id, created_at, snapshot_hash, artifact_path, redaction_applied) VALUES (?, ?, ?, ?, ?, ?)",
            (snapshot_id, request_id, created_at, snapshot_hash, artifact_path, int(redaction_applied)),
        )

    def insert_run(
        self,
        *,
        run_id: str,
        request_id: str,
        pod_id: str,
        dna_id: str,
        snapshot_id: str,
        now_slice_id: Optional[str],
        created_at: str,
        status: str,
        latency_ms: int,
        executor_output_id: Optional[str],
        judge_result_id: Optional[str],
        trace_fp: str,
        behavior_fp: str,
        tool_seq_fp: Optional[str],
        attempt_count: int = 1,
        winner_attempt_num: int = 1,
        winner_attempt_id: Optional[str] = None,
        repaired: bool = False,
        winner_executor_output_artifact_path: Optional[str] = None,
        winner_judge_result_artifact_path: Optional[str] = None,
    ) -> None:
        self.execute(
            """
            INSERT INTO runs (
                run_id, request_id, pod_id, dna_id, snapshot_id, now_slice_id, executor_output_id, judge_result_id,
                status, latency_ms, trace_fp, behavior_fp, tool_seq_fp, attempt_count,
                winner_attempt_num, winner_attempt_id, repaired,
                winner_executor_output_artifact_path, winner_judge_result_artifact_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              request_id = excluded.request_id,
              pod_id = excluded.pod_id,
              dna_id = excluded.dna_id,
              snapshot_id = excluded.snapshot_id,
              now_slice_id = excluded.now_slice_id,
              executor_output_id = excluded.executor_output_id,
              judge_result_id = excluded.judge_result_id,
              status = excluded.status,
              latency_ms = excluded.latency_ms,
              trace_fp = excluded.trace_fp,
              behavior_fp = excluded.behavior_fp,
              tool_seq_fp = excluded.tool_seq_fp,
              attempt_count = excluded.attempt_count,
              winner_attempt_num = excluded.winner_attempt_num,
              winner_attempt_id = excluded.winner_attempt_id,
              repaired = excluded.repaired,
              winner_executor_output_artifact_path = excluded.winner_executor_output_artifact_path,
              winner_judge_result_artifact_path = excluded.winner_judge_result_artifact_path
            """,
            (
                run_id,
                request_id,
                pod_id,
                dna_id,
                snapshot_id,
                now_slice_id,
                executor_output_id,
                judge_result_id,
                status,
                latency_ms,
                trace_fp,
                behavior_fp,
                tool_seq_fp,
                int(attempt_count),
                int(winner_attempt_num),
                winner_attempt_id,
                int(repaired),
                winner_executor_output_artifact_path,
                winner_judge_result_artifact_path,
                created_at,
            ),
        )

    def create_run(
        self,
        *,
        run_id: str,
        request_id: str,
        pod_id: str,
        dna_id: str,
        snapshot_id: str,
        now_slice_id: Optional[str] = None,
        created_at: str,
    ) -> str:
        # Runs are summary-only; seed with neutral placeholders and finalize after attempts.
        self.insert_run(
            run_id=run_id,
            request_id=request_id,
            pod_id=pod_id,
            dna_id=dna_id,
            snapshot_id=snapshot_id,
            now_slice_id=now_slice_id,
            created_at=created_at,
            status="failed",
            latency_ms=0,
            executor_output_id=None,
            judge_result_id=None,
            trace_fp="fp_pending",
            behavior_fp="fp_pending",
            tool_seq_fp=None,
            attempt_count=0,
            winner_attempt_num=1,
            winner_attempt_id=None,
            repaired=False,
            winner_executor_output_artifact_path=None,
            winner_judge_result_artifact_path=None,
        )
        return run_id

    def insert_now_slice(
        self,
        *,
        now_slice_id: str,
        request_id: str,
        snapshot_id: str,
        created_at: str,
        now_hash: str,
        now_band: str,
        execution_permission: bool,
        persona_id: str,
        persona_version: str,
        handoff_artifact_id: Optional[str],
        allowed_actions_json: str,
        budget_json: str,
        policy_versions_json: str,
        artifact_path: str,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO now_slices (
                now_slice_id, request_id, snapshot_id, created_at, now_hash, now_band, execution_permission,
                persona_id, persona_version, handoff_artifact_id,
                allowed_actions_json, budget_json, policy_versions_json, artifact_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_slice_id,
                request_id,
                snapshot_id,
                created_at,
                now_hash,
                now_band,
                int(execution_permission),
                persona_id,
                persona_version,
                handoff_artifact_id,
                allowed_actions_json,
                budget_json,
                policy_versions_json,
                artifact_path,
            ),
        )

    def insert_run_attempt(
        self,
        *,
        run_id: str,
        attempt_num: int,
        snapshot_path: str,
        created_at: str,
        status: str,
        latency_ms: int,
        trace_fp: str,
        behavior_fp: str,
        tool_seq_fp: Optional[str],
        passed: bool,
        scores_json: Optional[str],
        tags_json: Optional[str],
        failures_json: Optional[str],
        executor_output_id: Optional[str],
        judge_result_id: Optional[str],
        artifact_executor_path: Optional[str],
        artifact_judge_path: Optional[str],
        dream_grid_json: Optional[str] = None,
        dream_grid_fp: Optional[str] = None,
        dream_density: float = 0.0,
        dream_entropy: float = 0.0,
        dream_popcount: int = 0,
        dream_largest_component: int = 0,
        dream_symmetry: float = 0.0,
        executor_prompt_template_id: Optional[str] = None,
        executor_prompt_hash: Optional[str] = None,
        judge_prompt_template_id: Optional[str] = None,
        judge_prompt_hash: Optional[str] = None,
        retry_prompt_template_id: Optional[str] = None,
        retry_prompt_hash: Optional[str] = None,
        inference_params_json: Optional[str] = None,
        token_counts_json: Optional[str] = None,
        context_truncated: bool = False,
        truncation_reason: Optional[str] = None,
        error_id: Optional[str] = None,
        artifact_error_path: Optional[str] = None,
        persona_id: str = "general",
        persona_version: str = "pv_unknown",
        handoff_artifact_id: Optional[str] = None,
        workspace_ops_count: int = 0,
        bytes_written: int = 0,
        knowledge_reads_count: int = 0,
        knowledge_commits_count: int = 0,
        knowledge_commit_attempts: int = 0,
        knowledge_commit_pass_rate: float = 0.0,
        source_artifact_ids_json: Optional[str] = None,
        attempt_id: Optional[str] = None,
    ) -> str:
        resolved_attempt_id = attempt_id or f"att_{abs(hash((run_id, attempt_num, created_at))) % (10**12):012d}"
        self.execute(
            """
            INSERT OR REPLACE INTO run_attempts (
                attempt_id, run_id, attempt_num, created_at, status, latency_ms,
                executor_output_id, judge_result_id, trace_fp, behavior_fp, tool_seq_fp,
                artifact_snapshot_path, artifact_executor_path, artifact_judge_path, pass,
                dream_grid_json, dream_grid_fp, dream_density, dream_entropy, dream_popcount,
                dream_largest_component, dream_symmetry,
                executor_prompt_template_id, executor_prompt_hash,
                judge_prompt_template_id, judge_prompt_hash,
                retry_prompt_template_id, retry_prompt_hash,
                inference_params_json, token_counts_json, context_truncated, truncation_reason,
                error_id, artifact_error_path,
                persona_id, persona_version, handoff_artifact_id,
                workspace_ops_count, bytes_written, knowledge_reads_count, knowledge_commits_count,
                knowledge_commit_attempts, knowledge_commit_pass_rate, source_artifact_ids_json,
                scores_json, tags_json, failures_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_attempt_id,
                run_id,
                int(attempt_num),
                created_at,
                status,
                int(latency_ms),
                executor_output_id,
                judge_result_id,
                trace_fp,
                behavior_fp,
                tool_seq_fp,
                snapshot_path,
                artifact_executor_path,
                artifact_judge_path,
                int(passed),
                dream_grid_json,
                dream_grid_fp,
                float(dream_density),
                float(dream_entropy),
                int(dream_popcount),
                int(dream_largest_component),
                float(dream_symmetry),
                executor_prompt_template_id,
                executor_prompt_hash,
                judge_prompt_template_id,
                judge_prompt_hash,
                retry_prompt_template_id,
                retry_prompt_hash,
                inference_params_json,
                token_counts_json,
                int(context_truncated),
                truncation_reason,
                error_id,
                artifact_error_path,
                persona_id,
                persona_version,
                handoff_artifact_id,
                int(workspace_ops_count),
                int(bytes_written),
                int(knowledge_reads_count),
                int(knowledge_commits_count),
                int(knowledge_commit_attempts),
                float(knowledge_commit_pass_rate),
                source_artifact_ids_json,
                scores_json,
                tags_json,
                failures_json,
            ),
        )
        return resolved_attempt_id

    def finalize_run_summary(
        self,
        *,
        run_id: str,
        status: str,
        latency_ms: int,
        attempt_count: int,
        winner_attempt_num: int,
        repaired: bool,
        winner_attempt_id: str,
        executor_output_id: Optional[str],
        judge_result_id: Optional[str],
        trace_fp: str,
        behavior_fp: str,
        tool_seq_fp: Optional[str],
        winner_executor_output_artifact_path: Optional[str],
        winner_judge_result_artifact_path: Optional[str],
    ) -> None:
        self.execute(
            """
            UPDATE runs
            SET status = ?,
                latency_ms = ?,
                attempt_count = ?,
                winner_attempt_num = ?,
                winner_attempt_id = ?,
                repaired = ?,
                winner_executor_output_artifact_path = ?,
                winner_judge_result_artifact_path = ?,
                executor_output_id = ?,
                judge_result_id = ?,
                trace_fp = ?,
                behavior_fp = ?,
                tool_seq_fp = ?
            WHERE run_id = ?
            """,
            (
                status,
                int(latency_ms),
                int(attempt_count),
                int(winner_attempt_num),
                winner_attempt_id,
                int(repaired),
                winner_executor_output_artifact_path,
                winner_judge_result_artifact_path,
                executor_output_id,
                judge_result_id,
                trace_fp,
                behavior_fp,
                tool_seq_fp,
                run_id,
            ),
        )

    def insert_executor_output(
        self,
        *,
        executor_output_id: str,
        run_id: str,
        created_at: str,
        output_hash: str,
        artifact_path: str,
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO executor_outputs (executor_output_id, run_id, created_at, output_hash, artifact_path) VALUES (?, ?, ?, ?, ?)",
            (executor_output_id, run_id, created_at, output_hash, artifact_path),
        )

    def insert_judge_result(
        self,
        *,
        judge_result_id: str,
        run_id: str,
        created_at: str,
        passed: bool,
        scores_json: str,
        tags_json: str,
        failures_json: Optional[str],
        feedback_internal: Optional[str],
        result_hash: str,
        artifact_path: str,
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO judge_results (judge_result_id, run_id, created_at, pass, scores_json, tags_json, failures_json, feedback_internal, result_hash, artifact_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                judge_result_id,
                run_id,
                created_at,
                int(passed),
                scores_json,
                tags_json,
                failures_json,
                feedback_internal,
                result_hash,
                artifact_path,
            ),
        )

    def insert_run_error(
        self,
        *,
        error_id: str,
        attempt_id: str,
        run_id: str,
        created_at: str,
        error_type: str,
        stage: Optional[str],
        reason_code: Optional[str],
        detail: Optional[str],
        offending_signal_json: Optional[str],
        stack_summary: Optional[str],
        artifact_path: str,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO run_errors (
                error_id, attempt_id, run_id, created_at, error_type, stage, reason_code, detail,
                offending_signal_json, stack_summary, artifact_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                error_id,
                attempt_id,
                run_id,
                created_at,
                error_type,
                stage,
                reason_code,
                detail,
                offending_signal_json,
                stack_summary,
                artifact_path,
            ),
        )

    def insert_tool_call(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        created_at: str,
        tool: str,
        args_hash: str,
        args_artifact_path: str,
        allowed: bool,
        blocked_reason: Optional[str],
        started_at: Optional[str],
        ended_at: Optional[str],
        result_artifact_path: Optional[str],
        error_type: Optional[str],
        error_message: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO tool_calls (tool_call_id, run_id, created_at, tool, args_hash, args_artifact_path, allowed, blocked_reason, started_at, ended_at, result_artifact_path, error_type, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tool_call_id,
                run_id,
                created_at,
                tool,
                args_hash,
                args_artifact_path,
                int(allowed),
                blocked_reason,
                started_at,
                ended_at,
                result_artifact_path,
                error_type,
                error_message,
            ),
        )

    def insert_lineage_edge(
        self,
        *,
        edge_id: str,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
        reason: str,
        run_id: Optional[str],
        created_at: str,
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO lineage_edges (edge_id, parent_type, parent_id, child_type, child_id, reason, run_id, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (edge_id, parent_type, parent_id, child_type, child_id, reason, run_id, created_at, metadata_json),
        )

    def insert_artifact_registry(
        self,
        *,
        artifact_id: str,
        created_at: str,
        artifact_type: str,
        content_hash: str,
        artifact_path: str,
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO artifacts (artifact_id, created_at, artifact_type, content_hash, artifact_path, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
            (artifact_id, created_at, artifact_type, content_hash, artifact_path, metadata_json),
        )

    def upsert_routing_weight(self, *, pod_id: str, updated_at: str, weight: float, metadata_json: Optional[str]) -> None:
        self.execute(
            "INSERT OR REPLACE INTO routing_weights (pod_id, updated_at, weight, metadata_json) VALUES (?, ?, ?, ?)",
            (pod_id, updated_at, weight, metadata_json),
        )

    def upsert_routing_weight_by_type(
        self,
        *,
        request_type: str,
        pod_id: str,
        updated_at: str,
        weight: float,
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO routing_weights_by_type (request_type, pod_id, updated_at, weight, metadata_json) VALUES (?, ?, ?, ?, ?)",
            (request_type, pod_id, updated_at, weight, metadata_json),
        )

    def fetch_snapshot_path(self, snapshot_id: str) -> Optional[str]:
        row = self.fetchone("SELECT artifact_path FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
        return None if row is None else str(row["artifact_path"])

    def fetch_now_slice_path(self, now_slice_id: str) -> Optional[str]:
        row = self.fetchone("SELECT artifact_path FROM now_slices WHERE now_slice_id = ?", (now_slice_id,))
        return None if row is None else str(row["artifact_path"])

    def insert_external_signal(
        self,
        *,
        signal_id: str,
        created_at: str,
        request_id: Optional[str],
        pod_id: Optional[str],
        request_type: Optional[str],
        signal_type: str,
        value: float,
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO external_signals (signal_id, created_at, request_id, pod_id, request_type, signal_type, value, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (signal_id, created_at, request_id, pod_id, request_type, signal_type, value, metadata_json),
        )

    def fetch_request_created_at(self, request_id: str) -> Optional[str]:
        row = self.fetchone("SELECT created_at FROM requests WHERE request_id = ?", (request_id,))
        return None if row is None else str(row["created_at"])

    def fetch_run_by_id(self, run_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT run_id, request_id, pod_id, created_at FROM runs WHERE run_id = ?", (run_id,))

    def count_signals(self, *, pod_id: str, signal_type: str, request_type: Optional[str] = None) -> int:
        if request_type is None:
            row = self.fetchone(
                "SELECT COUNT(1) AS c FROM external_signals WHERE pod_id = ? AND signal_type = ?",
                (pod_id, signal_type),
            )
        else:
            row = self.fetchone(
                "SELECT COUNT(1) AS c FROM external_signals WHERE pod_id = ? AND request_type = ? AND signal_type = ?",
                (pod_id, request_type, signal_type),
            )
        return 0 if row is None else int(row["c"])

    def avg_signal(self, *, pod_id: str, signal_type: str, request_type: Optional[str] = None) -> float:
        if request_type is None:
            row = self.fetchone(
                "SELECT AVG(value) AS v FROM external_signals WHERE pod_id = ? AND signal_type = ?",
                (pod_id, signal_type),
            )
        else:
            row = self.fetchone(
                "SELECT AVG(value) AS v FROM external_signals WHERE pod_id = ? AND request_type = ? AND signal_type = ?",
                (pod_id, request_type, signal_type),
            )
        if row is None or row["v"] is None:
            return 0.0
        return float(row["v"])

    def load_routing_weights(self) -> dict[str, float]:
        rows = self.fetchall("SELECT pod_id, weight FROM routing_weights")
        return {str(r["pod_id"]): float(r["weight"]) for r in rows}

    def load_routing_weights_by_type(self, *, request_type: str) -> dict[str, float]:
        rows = self.fetchall(
            "SELECT pod_id, weight FROM routing_weights_by_type WHERE request_type = ?",
            (request_type,),
        )
        return {str(r["pod_id"]): float(r["weight"]) for r in rows}

    def leaderboard_for_request_type(self, *, request_type: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.fetchall(
            "SELECT pod_id, weight FROM routing_weights_by_type WHERE request_type = ? ORDER BY weight DESC, pod_id ASC LIMIT ?",
            (request_type, limit),
        )
        return [{"pod_id": str(r["pod_id"]), "weight": float(r["weight"])} for r in rows]

    def upsert_pod_resource_state(
        self,
        *,
        request_type: str,
        pod_id: str,
        updated_at: str,
        compute_budget: float,
        traffic_cap: float,
        incubation_budget: int,
        is_starved: bool,
        assigned_requests: int,
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO pod_resource_state (request_type, pod_id, updated_at, compute_budget, traffic_cap, incubation_budget, is_starved, assigned_requests, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_type,
                pod_id,
                updated_at,
                float(compute_budget),
                float(traffic_cap),
                int(incubation_budget),
                int(is_starved),
                int(assigned_requests),
                metadata_json,
            ),
        )

    def get_pod_resource_state(self, *, request_type: str, pod_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM pod_resource_state WHERE request_type = ? AND pod_id = ?",
            (request_type, pod_id),
        )

    def list_pod_resource_state(self, *, request_type: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM pod_resource_state WHERE request_type = ? ORDER BY pod_id",
            (request_type,),
        )

    def prior_request_count(self, *, user_input: str, request_type: str) -> int:
        row = self.fetchone(
            "SELECT COUNT(1) AS c FROM requests WHERE user_input = ? AND request_type = ?",
            (user_input, request_type),
        )
        return 0 if row is None else int(row["c"])

    def total_signal_count(self, signal_type: Optional[str] = None) -> int:
        if signal_type is None:
            row = self.fetchone("SELECT COUNT(1) AS c FROM external_signals")
        else:
            row = self.fetchone("SELECT COUNT(1) AS c FROM external_signals WHERE signal_type = ?", (signal_type,))
        return 0 if row is None else int(row["c"])

    def create_action_approval(
        self,
        *,
        approval_id: str,
        created_at: str,
        tool: str,
        request_id: Optional[str],
        pod_id: Optional[str],
        expires_at: Optional[str],
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO action_approvals (approval_id, created_at, approved_at, status, tool, request_id, pod_id, expires_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (approval_id, created_at, None, "pending", tool, request_id, pod_id, expires_at, metadata_json),
        )

    def approve_action(self, *, approval_id: str, approved_at: str) -> None:
        self.execute(
            "UPDATE action_approvals SET status = 'approved', approved_at = ? WHERE approval_id = ?",
            (approved_at, approval_id),
        )

    def is_action_approved(self, approval_id: str) -> bool:
        row = self.fetchone(
            "SELECT status, expires_at FROM action_approvals WHERE approval_id = ?",
            (approval_id,),
        )
        if row is None:
            return False
        status = str(row["status"])
        if status != "approved":
            return False
        expires_at = row["expires_at"]
        if expires_at is None:
            return True
        return True  # simple v1; can enforce timestamp in v2.

    def action_idempotency_exists(self, idempotency_key: str) -> bool:
        row = self.fetchone(
            "SELECT idempotency_key FROM action_idempotency WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return row is not None

    def insert_action_idempotency(
        self,
        *,
        idempotency_key: str,
        created_at: str,
        tool: str,
        run_id: Optional[str],
        rollback_hint: Optional[str],
        status: str,
        metadata_json: Optional[str],
    ) -> None:
        self.execute(
            "INSERT OR REPLACE INTO action_idempotency (idempotency_key, created_at, tool, run_id, rollback_hint, status, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (idempotency_key, created_at, tool, run_id, rollback_hint, status, metadata_json),
        )

    def top_trace_fingerprints(self, *, pod_id: str, last_n_runs: int = 100, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.fetchall(
            """
            SELECT trace_fp, COUNT(1) AS run_count
            FROM (
                SELECT trace_fp
                FROM runs
                WHERE pod_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            GROUP BY trace_fp
            ORDER BY run_count DESC, trace_fp ASC
            LIMIT ?
            """,
            (pod_id, last_n_runs, limit),
        )
        return [{"trace_fp": str(r["trace_fp"]), "run_count": int(r["run_count"])} for r in rows]

    def top_behavior_fingerprints(self, *, pod_id: str, last_n_runs: int = 100, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.fetchall(
            """
            SELECT behavior_fp, COUNT(1) AS run_count
            FROM (
                SELECT behavior_fp
                FROM runs
                WHERE pod_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            GROUP BY behavior_fp
            ORDER BY run_count DESC, behavior_fp ASC
            LIMIT ?
            """,
            (pod_id, last_n_runs, limit),
        )
        return [{"behavior_fp": str(r["behavior_fp"]), "run_count": int(r["run_count"])} for r in rows]

    def run_attempts_for_run(self, *, run_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT attempt_id, attempt_num, created_at, status, latency_ms, executor_output_id, judge_result_id,
                   trace_fp, behavior_fp, tool_seq_fp, artifact_snapshot_path, artifact_executor_path,
                   artifact_judge_path, pass, dream_grid_json, dream_grid_fp, dream_density, dream_entropy,
                   dream_popcount, dream_largest_component, dream_symmetry,
                   executor_prompt_template_id, executor_prompt_hash,
                   judge_prompt_template_id, judge_prompt_hash,
                   retry_prompt_template_id, retry_prompt_hash,
                   inference_params_json, token_counts_json, context_truncated, truncation_reason,
                   error_id, artifact_error_path,
                   persona_id, persona_version, handoff_artifact_id,
                   workspace_ops_count, bytes_written, knowledge_reads_count, knowledge_commits_count,
                   knowledge_commit_attempts, knowledge_commit_pass_rate, source_artifact_ids_json,
                   scores_json, tags_json, failures_json
            FROM run_attempts
            WHERE run_id = ?
            ORDER BY attempt_num ASC
            """,
            (run_id,),
        )

    def list_run_attempts(self, run_id: str) -> list[sqlite3.Row]:
        return self.run_attempts_for_run(run_id=run_id)

    def get_winner_attempt(self, run_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            """
            SELECT ra.*
            FROM runs r
            JOIN run_attempts ra
              ON ra.run_id = r.run_id
             AND (
                 (r.winner_attempt_id IS NOT NULL AND ra.attempt_id = r.winner_attempt_id)
                 OR (r.winner_attempt_id IS NULL AND ra.attempt_num = r.winner_attempt_num)
             )
            WHERE r.run_id = ?
            """,
            (run_id,),
        )

    def get_run_attempt(self, attempt_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM run_attempts WHERE attempt_id = ?", (attempt_id,))

    def insert_workspace_lease(
        self,
        *,
        lease_id: str,
        run_id: str,
        attempt_id: str,
        created_at: str,
        expires_at: str,
        capabilities_json: str,
        roots_json: str,
        budgets_json: str,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO workspace_leases (
                lease_id, run_id, attempt_id, created_at, expires_at,
                capabilities_json, roots_json, budgets_json, ops_used, bytes_used, files_used
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT ops_used FROM workspace_leases WHERE lease_id = ?), 0),
                      COALESCE((SELECT bytes_used FROM workspace_leases WHERE lease_id = ?), 0),
                      COALESCE((SELECT files_used FROM workspace_leases WHERE lease_id = ?), 0))
            """,
            (
                lease_id,
                run_id,
                attempt_id,
                created_at,
                expires_at,
                capabilities_json,
                roots_json,
                budgets_json,
                lease_id,
                lease_id,
                lease_id,
            ),
        )

    def get_workspace_lease(self, lease_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM workspace_leases WHERE lease_id = ?", (lease_id,))

    def update_workspace_lease_usage(self, *, lease_id: str, ops_delta: int, bytes_delta: int, files_delta: int) -> None:
        self.execute(
            """
            UPDATE workspace_leases
            SET ops_used = ops_used + ?,
                bytes_used = bytes_used + ?,
                files_used = files_used + ?
            WHERE lease_id = ?
            """,
            (int(ops_delta), int(bytes_delta), int(files_delta), lease_id),
        )

    def insert_workspace_op_event(
        self,
        *,
        op_id: str,
        lease_id: str,
        run_id: str,
        attempt_id: str,
        created_at: str,
        op_type: str,
        target_path: Optional[str],
        bytes_delta: int,
        files_delta: int,
        status: str,
        detail: Optional[str],
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO workspace_op_events (
                op_id, lease_id, run_id, attempt_id, created_at, op_type, target_path,
                bytes_delta, files_delta, status, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                lease_id,
                run_id,
                attempt_id,
                created_at,
                op_type,
                target_path,
                int(bytes_delta),
                int(files_delta),
                status,
                detail,
            ),
        )

    def apply_attempt_workspace_metrics(
        self,
        *,
        attempt_id: str,
        workspace_ops_delta: int = 0,
        bytes_written_delta: int = 0,
        knowledge_reads_delta: int = 0,
        knowledge_commit_attempted_delta: int = 0,
        knowledge_commits_delta: int = 0,
        source_artifact_ids: Optional[list[str]] = None,
    ) -> None:
        row = self.get_run_attempt(attempt_id)
        if row is None:
            return
        existing_sources = json.loads(str(row["source_artifact_ids_json"] or "[]"))
        merged_sources = sorted(set(str(x) for x in existing_sources + (source_artifact_ids or [])))
        next_attempts = int(row["knowledge_commit_attempts"] or 0) + int(knowledge_commit_attempted_delta)
        next_commits = int(row["knowledge_commits_count"] or 0) + int(knowledge_commits_delta)
        pass_rate = float(next_commits / next_attempts) if next_attempts > 0 else 0.0
        self.execute(
            """
            UPDATE run_attempts
            SET workspace_ops_count = workspace_ops_count + ?,
                bytes_written = bytes_written + ?,
                knowledge_reads_count = knowledge_reads_count + ?,
                knowledge_commit_attempts = knowledge_commit_attempts + ?,
                knowledge_commits_count = knowledge_commits_count + ?,
                knowledge_commit_pass_rate = ?,
                source_artifact_ids_json = ?
            WHERE attempt_id = ?
            """,
            (
                int(workspace_ops_delta),
                int(bytes_written_delta),
                int(knowledge_reads_delta),
                int(knowledge_commit_attempted_delta),
                int(knowledge_commits_delta),
                pass_rate,
                json.dumps(merged_sources),
                attempt_id,
            ),
        )

    def upsert_knowledge_doc(self, *, doc_id: str, doc_key: str, created_at: str, updated_at: str, latest_version_num: int) -> None:
        self.execute(
            """
            INSERT INTO knowledge_docs (doc_id, doc_key, created_at, updated_at, latest_version_num)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(doc_key) DO UPDATE SET
              updated_at = excluded.updated_at,
              latest_version_num = excluded.latest_version_num
            """,
            (doc_id, doc_key, created_at, updated_at, int(latest_version_num)),
        )

    def get_knowledge_doc_by_key(self, doc_key: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM knowledge_docs WHERE doc_key = ?", (doc_key,))

    def get_latest_knowledge_version(self, doc_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            """
            SELECT *
            FROM knowledge_doc_versions
            WHERE doc_id = ?
            ORDER BY version_num DESC
            LIMIT 1
            """,
            (doc_id,),
        )

    def insert_knowledge_version(
        self,
        *,
        version_id: str,
        doc_id: str,
        version_num: int,
        created_at: str,
        title: Optional[str],
        summary: str,
        extracted_facts_json: str,
        source_artifact_ids_json: str,
        content_hash: str,
        artifact_path: str,
        created_by_run_id: str,
        created_by_attempt_id: str,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO knowledge_doc_versions (
                version_id, doc_id, version_num, created_at, title, summary,
                extracted_facts_json, source_artifact_ids_json, content_hash, artifact_path,
                created_by_run_id, created_by_attempt_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id,
                doc_id,
                int(version_num),
                created_at,
                title,
                summary,
                extracted_facts_json,
                source_artifact_ids_json,
                content_hash,
                artifact_path,
                created_by_run_id,
                created_by_attempt_id,
            ),
        )

    def insert_knowledge_commit(
        self,
        *,
        commit_id: str,
        lease_id: str,
        run_id: str,
        attempt_id: str,
        created_at: str,
        passed: bool,
        reason: Optional[str],
        doc_key: str,
        doc_id: Optional[str],
        version_id: Optional[str],
        source_artifact_ids_json: str,
    ) -> None:
        self.execute(
            """
            INSERT OR REPLACE INTO knowledge_commits (
                commit_id, lease_id, run_id, attempt_id, created_at, pass, reason,
                doc_key, doc_id, version_id, source_artifact_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commit_id,
                lease_id,
                run_id,
                attempt_id,
                created_at,
                int(passed),
                reason,
                doc_key,
                doc_id,
                version_id,
                source_artifact_ids_json,
            ),
        )

    def search_knowledge_versions(self, *, query: str, limit: int = 20) -> list[sqlite3.Row]:
        q = f"%{query.lower()}%"
        return self.fetchall(
            """
            SELECT d.doc_key, d.doc_id, d.latest_version_num, v.version_id, v.version_num, v.title, v.summary,
                   v.source_artifact_ids_json, v.created_at, v.created_by_run_id, v.created_by_attempt_id
            FROM knowledge_docs d
            JOIN knowledge_doc_versions v ON v.doc_id = d.doc_id AND v.version_num = d.latest_version_num
            WHERE LOWER(COALESCE(v.title, '')) LIKE ? OR LOWER(v.summary) LIKE ? OR LOWER(v.extracted_facts_json) LIKE ?
            ORDER BY v.created_at DESC
            LIMIT ?
            """,
            (q, q, q, int(limit)),
        )

    def top_behavior_fps_with_repair_rate(
        self,
        *,
        window: int,
        limit: int,
        pod_id: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        if pod_id is None:
            return self.fetchall(
                """
                WITH recent AS (
                  SELECT run_id, pod_id, dna_id, behavior_fp, repaired
                  FROM runs
                  ORDER BY created_at DESC
                  LIMIT ?
                )
                SELECT behavior_fp,
                       COUNT(1) AS count_total,
                       SUM(CASE WHEN repaired = 1 THEN 1 ELSE 0 END) AS count_repaired
                FROM recent
                GROUP BY behavior_fp
                ORDER BY count_total DESC, behavior_fp ASC
                LIMIT ?
                """,
                (window, limit),
            )
        return self.fetchall(
            """
            WITH recent AS (
              SELECT run_id, pod_id, dna_id, behavior_fp, repaired
              FROM runs
              WHERE pod_id = ?
              ORDER BY created_at DESC
              LIMIT ?
            )
            SELECT behavior_fp,
                   COUNT(1) AS count_total,
                   SUM(CASE WHEN repaired = 1 THEN 1 ELSE 0 END) AS count_repaired
            FROM recent
            GROUP BY behavior_fp
            ORDER BY count_total DESC, behavior_fp ASC
            LIMIT ?
            """,
            (pod_id, window, limit),
        )

    def sample_runs_for_behavior_fps(
        self,
        *,
        behavior_fps: list[str],
        window: int,
        sample_size: int = 3,
        pod_id: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        if not behavior_fps:
            return []
        placeholders = ",".join(["?"] * len(behavior_fps))
        if pod_id is None:
            return self.fetchall(
                f"""
                WITH recent AS (
                  SELECT run_id, behavior_fp, winner_attempt_num
                  FROM runs
                  ORDER BY created_at DESC
                  LIMIT ?
                ),
                ranked AS (
                  SELECT run_id, behavior_fp, winner_attempt_num,
                         ROW_NUMBER() OVER (PARTITION BY behavior_fp ORDER BY run_id ASC) AS rn
                  FROM recent
                  WHERE behavior_fp IN ({placeholders})
                )
                SELECT run_id, behavior_fp, winner_attempt_num
                FROM ranked
                WHERE rn <= ?
                ORDER BY behavior_fp ASC, rn ASC
                """,
                (window, *behavior_fps, sample_size),
            )
        return self.fetchall(
            f"""
            WITH recent AS (
              SELECT run_id, behavior_fp, winner_attempt_num
              FROM runs
              WHERE pod_id = ?
              ORDER BY created_at DESC
              LIMIT ?
            ),
            ranked AS (
              SELECT run_id, behavior_fp, winner_attempt_num,
                     ROW_NUMBER() OVER (PARTITION BY behavior_fp ORDER BY run_id ASC) AS rn
              FROM recent
              WHERE behavior_fp IN ({placeholders})
            )
            SELECT run_id, behavior_fp, winner_attempt_num
            FROM ranked
            WHERE rn <= ?
            ORDER BY behavior_fp ASC, rn ASC
            """,
            (pod_id, window, *behavior_fps, sample_size),
        )

    def attractor_rows(
        self,
        *,
        window: int,
        group_by: str,
    ) -> list[sqlite3.Row]:
        if group_by == "pod":
            return self.fetchall(
                """
                WITH ranked AS (
                    SELECT r.run_id, r.pod_id, r.dna_id, r.behavior_fp, r.winner_attempt_num, r.attempt_count, r.repaired, r.latency_ms,
                           COALESCE(jr.pass, 0) AS pass,
                           COALESCE(ra.attempt_id, '') AS winner_attempt_id,
                           COALESCE(ra.workspace_ops_count, 0) AS workspace_ops_count,
                           COALESCE(ra.knowledge_commit_attempts, 0) AS knowledge_commit_attempts,
                           COALESCE(ra.knowledge_commits_count, 0) AS knowledge_commits_count,
                           r.created_at,
                           ROW_NUMBER() OVER (PARTITION BY r.pod_id ORDER BY r.created_at DESC) AS rn
                    FROM runs r
                    LEFT JOIN judge_results jr ON jr.run_id = r.run_id
                    LEFT JOIN run_attempts ra
                      ON ra.run_id = r.run_id
                     AND (
                       (r.winner_attempt_id IS NOT NULL AND ra.attempt_id = r.winner_attempt_id)
                       OR (r.winner_attempt_id IS NULL AND ra.attempt_num = r.winner_attempt_num)
                     )
                    WHERE r.behavior_fp != 'fp_pending'
                )
                SELECT *
                FROM ranked
                WHERE rn <= ?
                ORDER BY pod_id ASC, created_at DESC
                """,
                (window,),
            )
        return self.fetchall(
            """
            WITH recent AS (
                SELECT r.run_id, r.pod_id, r.dna_id, r.behavior_fp, r.winner_attempt_num, r.attempt_count, r.repaired, r.latency_ms,
                       COALESCE(jr.pass, 0) AS pass,
                       COALESCE(ra.attempt_id, '') AS winner_attempt_id,
                       COALESCE(ra.workspace_ops_count, 0) AS workspace_ops_count,
                       COALESCE(ra.knowledge_commit_attempts, 0) AS knowledge_commit_attempts,
                       COALESCE(ra.knowledge_commits_count, 0) AS knowledge_commits_count,
                       r.created_at
                FROM runs r
                LEFT JOIN judge_results jr ON jr.run_id = r.run_id
                LEFT JOIN run_attempts ra
                  ON ra.run_id = r.run_id
                 AND (
                   (r.winner_attempt_id IS NOT NULL AND ra.attempt_id = r.winner_attempt_id)
                   OR (r.winner_attempt_id IS NULL AND ra.attempt_num = r.winner_attempt_num)
                 )
                WHERE r.behavior_fp != 'fp_pending'
                ORDER BY r.created_at DESC
                LIMIT ?
            )
            SELECT *
            FROM recent
            ORDER BY created_at DESC
            """,
            (window,),
        )

    def dna_pass_rate(self, *, dna_id: str, last_n_runs: int = 100) -> float:
        row = self.fetchone(
            """
            SELECT AVG(CAST(jr.pass AS REAL)) AS pass_rate
            FROM runs r
            JOIN judge_results jr ON jr.run_id = r.run_id
            WHERE r.dna_id = ?
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            (dna_id, last_n_runs),
        )
        if row is None or row["pass_rate"] is None:
            return 0.0
        return float(row["pass_rate"])
