-- core/storage/migrations.sql
-- SQLite schema for Agent Pods Prototype Spec v1

PRAGMA foreign_keys = ON;

-- ----------------------------
-- Requests (UI/API level)
-- ----------------------------
CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|error
  user_input TEXT NOT NULL,
  request_type TEXT NOT NULL,            -- for routing/specialization
  constraints_json TEXT                  -- JSON string
);

-- ----------------------------
-- Pods registry (static + metrics pointers)
-- ----------------------------
CREATE TABLE IF NOT EXISTS pods (
  pod_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  is_enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL              -- JSON string (pod config)
);

-- ----------------------------
-- Snapshots (frozen world view)
-- Store bulky JSON in artifact_store, keep hash + pointer here.
-- ----------------------------
CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL,           -- sha256 of canonical json
  artifact_path TEXT NOT NULL,           -- filesystem pointer
  redaction_applied INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_snapshots_request_id ON snapshots(request_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_hash ON snapshots(snapshot_hash);

-- ----------------------------
-- NOW slices (execution projection + constraints)
-- ----------------------------
CREATE TABLE IF NOT EXISTS now_slices (
  now_slice_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  now_hash TEXT NOT NULL,
  now_band TEXT NOT NULL DEFAULT 'local',       -- micro|local|sleep
  persona_id TEXT NOT NULL DEFAULT 'general',
  persona_version TEXT NOT NULL DEFAULT 'pv_unknown',
  handoff_artifact_id TEXT,
  execution_permission INTEGER NOT NULL DEFAULT 1,
  allowed_actions_json TEXT NOT NULL,           -- JSON array
  budget_json TEXT NOT NULL,                    -- JSON object
  policy_versions_json TEXT NOT NULL,           -- JSON object
  artifact_path TEXT NOT NULL,
  FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE CASCADE,
  FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_now_slices_request_id ON now_slices(request_id);
CREATE INDEX IF NOT EXISTS idx_now_slices_snapshot_id ON now_slices(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_now_slices_hash ON now_slices(now_hash);

-- ----------------------------
-- DNA versions (genotypes)
-- ----------------------------
CREATE TABLE IF NOT EXISTS dna_versions (
  dna_id TEXT PRIMARY KEY,               -- content hash id
  version INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  persona TEXT NOT NULL,                 -- planner|dev|tester|reviewer|release|maintainer|general
  dna_hash TEXT NOT NULL,                -- sha256 canonical
  artifact_path TEXT NOT NULL,           -- JSON stored in artifact store
  parents_json TEXT,                     -- JSON array of dna_ids
  mutation_id TEXT                       -- optional mutation id
);

CREATE INDEX IF NOT EXISTS idx_dna_hash ON dna_versions(dna_hash);
CREATE INDEX IF NOT EXISTS idx_dna_persona ON dna_versions(persona);

-- ----------------------------
-- Runs (one pod execution)
-- ----------------------------
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  pod_id TEXT NOT NULL,
  dna_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  now_slice_id TEXT,

  created_at TEXT NOT NULL,
  status TEXT NOT NULL,                  -- success|failed|blocked|retried
  latency_ms INTEGER NOT NULL DEFAULT 0,

  executor_output_id TEXT,
  judge_result_id TEXT,

  trace_fp TEXT NOT NULL,                -- run-level fingerprint (includes retry context)
  behavior_fp TEXT NOT NULL,             -- behavior-only fingerprint (attractor signal)
  tool_seq_fp TEXT,                      -- fingerprint of tool call sequence
  attempt_count INTEGER NOT NULL DEFAULT 1,
  winner_attempt_num INTEGER NOT NULL DEFAULT 1,
  winner_attempt_id TEXT,
  repaired INTEGER NOT NULL DEFAULT 0,
  winner_executor_output_artifact_path TEXT,
  winner_judge_result_artifact_path TEXT,

  FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE CASCADE,
  FOREIGN KEY (pod_id) REFERENCES pods(pod_id) ON DELETE RESTRICT,
  FOREIGN KEY (dna_id) REFERENCES dna_versions(dna_id) ON DELETE RESTRICT,
  FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id) ON DELETE RESTRICT,
  FOREIGN KEY (now_slice_id) REFERENCES now_slices(now_slice_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_request_id ON runs(request_id);
CREATE INDEX IF NOT EXISTS idx_runs_pod_id ON runs(pod_id);
CREATE INDEX IF NOT EXISTS idx_runs_dna_id ON runs(dna_id);
CREATE INDEX IF NOT EXISTS idx_runs_snapshot_id ON runs(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_runs_trace_fp ON runs(trace_fp);

-- ----------------------------
-- Run attempts (each try within one run)
-- ----------------------------
CREATE TABLE IF NOT EXISTS run_attempts (
  attempt_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  attempt_num INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,                  -- success|failed|blocked
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
  scores_json TEXT,
  tags_json TEXT,
  failures_json TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_attempts_run ON run_attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_run_attempts_behavior_fp ON run_attempts(behavior_fp);

-- ----------------------------
-- Run errors (separate from judge results)
-- ----------------------------
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
);

CREATE INDEX IF NOT EXISTS idx_run_errors_run_id ON run_errors(run_id);
CREATE INDEX IF NOT EXISTS idx_run_errors_type ON run_errors(error_type);

-- ----------------------------
-- Executor outputs (store pointer)
-- ----------------------------
CREATE TABLE IF NOT EXISTS executor_outputs (
  executor_output_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  output_hash TEXT NOT NULL,
  artifact_path TEXT NOT NULL,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_executor_outputs_hash ON executor_outputs(output_hash);

-- ----------------------------
-- Tool calls (trace + results)
-- ----------------------------
CREATE TABLE IF NOT EXISTS tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  created_at TEXT NOT NULL,

  tool TEXT NOT NULL,                    -- http_get|fs_read|fs_write|shell_exec|...
  args_hash TEXT NOT NULL,
  args_artifact_path TEXT NOT NULL,      -- args json in artifact store

  allowed INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,

  started_at TEXT,
  ended_at TEXT,

  result_artifact_path TEXT,             -- result json in artifact store
  error_type TEXT,
  error_message TEXT,

  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);

-- ----------------------------
-- Judge results (store pointer + summary scores)
-- ----------------------------
CREATE TABLE IF NOT EXISTS judge_results (
  judge_result_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,

  pass INTEGER NOT NULL DEFAULT 0,
  scores_json TEXT NOT NULL,             -- JSON dict of scores
  tags_json TEXT NOT NULL,               -- JSON array
  failures_json TEXT,                    -- JSON array
  feedback_internal TEXT,                -- small text; full report can be artifact too

  result_hash TEXT NOT NULL,
  artifact_path TEXT NOT NULL,

  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_judge_results_pass ON judge_results(pass);

-- ----------------------------
-- Artifacts (generic registry)
-- Points to filesystem artifact store items
-- ----------------------------
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,          -- sha256 id of content
  created_at TEXT NOT NULL,
  artifact_type TEXT NOT NULL,           -- snapshot|dna|executor_output|judge_result|rubric|tool_policy|other
  content_hash TEXT NOT NULL,
  artifact_path TEXT NOT NULL,
  metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);
CREATE INDEX IF NOT EXISTS idx_artifacts_hash ON artifacts(content_hash);

-- ----------------------------
-- Lineage edges (DNA/artifact survival tracking)
-- ----------------------------
CREATE TABLE IF NOT EXISTS lineage_edges (
  edge_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,

  parent_type TEXT NOT NULL,             -- dna|artifact
  parent_id TEXT NOT NULL,

  child_type TEXT NOT NULL,              -- dna|artifact
  child_id TEXT NOT NULL,

  reason TEXT NOT NULL,                  -- mutation|promotion|merge|manual_seed
  run_id TEXT,

  metadata_json TEXT,

  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage_edges(parent_type, parent_id);
CREATE INDEX IF NOT EXISTS idx_lineage_child ON lineage_edges(child_type, child_id);
CREATE INDEX IF NOT EXISTS idx_lineage_run ON lineage_edges(run_id);

-- ----------------------------
-- External signals (UI/environment selection pressure)
-- ----------------------------
CREATE TABLE IF NOT EXISTS external_signals (
  signal_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  request_id TEXT,
  pod_id TEXT,
  request_type TEXT,
  signal_type TEXT NOT NULL,             -- completion|retry|abandon|return|latency|thumbs_up|thumbs_down
  value REAL NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE SET NULL,
  FOREIGN KEY (pod_id) REFERENCES pods(pod_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_external_signals_pod ON external_signals(pod_id, signal_type);
CREATE INDEX IF NOT EXISTS idx_external_signals_request ON external_signals(request_id, signal_type);

-- ----------------------------
-- Routing weights (optional but handy)
-- ----------------------------
CREATE TABLE IF NOT EXISTS routing_weights (
  pod_id TEXT PRIMARY KEY,
  updated_at TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT,
  FOREIGN KEY (pod_id) REFERENCES pods(pod_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS routing_weights_by_type (
  request_type TEXT NOT NULL,
  pod_id TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT,
  PRIMARY KEY (request_type, pod_id),
  FOREIGN KEY (pod_id) REFERENCES pods(pod_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pod_resource_state (
  request_type TEXT NOT NULL,
  pod_id TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  compute_budget REAL NOT NULL DEFAULT 1.0,
  traffic_cap REAL NOT NULL DEFAULT 1.0,
  incubation_budget INTEGER NOT NULL DEFAULT 0,
  is_starved INTEGER NOT NULL DEFAULT 0,
  assigned_requests INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  PRIMARY KEY (request_type, pod_id),
  FOREIGN KEY (pod_id) REFERENCES pods(pod_id) ON DELETE CASCADE
);

-- ----------------------------
-- Safe autonomy: approvals + idempotency ledger
-- ----------------------------
CREATE TABLE IF NOT EXISTS action_approvals (
  approval_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  approved_at TEXT,
  status TEXT NOT NULL,                  -- pending|approved|rejected|expired
  tool TEXT NOT NULL,
  request_id TEXT,
  pod_id TEXT,
  expires_at TEXT,
  metadata_json TEXT,
  FOREIGN KEY (request_id) REFERENCES requests(request_id) ON DELETE SET NULL,
  FOREIGN KEY (pod_id) REFERENCES pods(pod_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS action_idempotency (
  idempotency_key TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  tool TEXT NOT NULL,
  run_id TEXT,
  rollback_hint TEXT,
  status TEXT NOT NULL,                  -- started|succeeded|failed
  metadata_json TEXT,
  FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE SET NULL
);

-- ----------------------------
-- Workspace leases + operations
-- ----------------------------
CREATE TABLE IF NOT EXISTS workspace_leases (
  lease_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  capabilities_json TEXT NOT NULL,       -- JSON array
  roots_json TEXT NOT NULL,              -- JSON array
  budgets_json TEXT NOT NULL,            -- JSON object
  ops_used INTEGER NOT NULL DEFAULT 0,
  bytes_used INTEGER NOT NULL DEFAULT 0,
  files_used INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_workspace_leases_run_attempt ON workspace_leases(run_id, attempt_id);

CREATE TABLE IF NOT EXISTS workspace_op_events (
  op_id TEXT PRIMARY KEY,
  lease_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  op_type TEXT NOT NULL,                 -- read|write|list|search|index|knowledge_read|knowledge_commit
  target_path TEXT,
  bytes_delta INTEGER NOT NULL DEFAULT 0,
  files_delta INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,                  -- allowed|blocked|error
  detail TEXT,
  FOREIGN KEY (lease_id) REFERENCES workspace_leases(lease_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workspace_ops_attempt ON workspace_op_events(attempt_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workspace_ops_lease ON workspace_op_events(lease_id, created_at);

-- ----------------------------
-- Knowledge store (append-only versions)
-- ----------------------------
CREATE TABLE IF NOT EXISTS knowledge_docs (
  doc_id TEXT PRIMARY KEY,
  doc_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  latest_version_num INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knowledge_doc_versions (
  version_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  version_num INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  title TEXT,
  summary TEXT NOT NULL,
  extracted_facts_json TEXT NOT NULL,    -- JSON array
  source_artifact_ids_json TEXT NOT NULL,-- JSON array
  content_hash TEXT NOT NULL,
  artifact_path TEXT NOT NULL,
  created_by_run_id TEXT NOT NULL,
  created_by_attempt_id TEXT NOT NULL,
  UNIQUE (doc_id, version_num),
  FOREIGN KEY (doc_id) REFERENCES knowledge_docs(doc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_versions_doc ON knowledge_doc_versions(doc_id, version_num DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_versions_hash ON knowledge_doc_versions(content_hash);

CREATE TABLE IF NOT EXISTS knowledge_commits (
  commit_id TEXT PRIMARY KEY,
  lease_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  attempt_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  pass INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  doc_key TEXT NOT NULL,
  doc_id TEXT,
  version_id TEXT,
  source_artifact_ids_json TEXT NOT NULL,
  FOREIGN KEY (lease_id) REFERENCES workspace_leases(lease_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_commits_attempt ON knowledge_commits(attempt_id, created_at);
