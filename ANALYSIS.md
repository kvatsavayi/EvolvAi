# EvolvAi — Comprehensive Architecture Analysis

> Generated: 2026-03-18 | Repository: `kvatsavayi/EvolvAi`

---

## 1. Repository Overview

**Total Python LOC:** ~15,000 across 70+ files  
**Tests:** 118 tests — **all passing** (81s runtime)  
**Stack:** Python 3.11 / FastAPI backend, React 18 + Vite frontend, SQLite persistence

### Directory Structure

```
evolvai/
├── apps/
│   ├── api/           # FastAPI REST API (main.py, routes.py, models.py, dependencies.py)
│   ├── mcp/           # Model Context Protocol server (JSON-RPC over stdio)
│   └── ui/            # React 18 + Vite frontend (src/main.jsx, styles.css)
├── core/
│   ├── executor/      # Executor engine (runner.py, prompts.py, sandbox.py, sanitize.py)
│   ├── judge/         # Judge evaluator (judge.py, validators.py, prompts.py, rubrics/)
│   ├── observability/ # Canonical hashing, traces, dream grids, metrics, logging
│   ├── pod/           # Pod runtime (pod.py, dna.py, lineage.py, mutation.py, selection.py, generator.py)
│   ├── regression/    # Regression harness for DNA mutations
│   ├── router/        # Request router (router.py, bandit.py, signals.py, classifier.py, resources.py)
│   ├── snapshot/      # NOW Slice / Snapshot schema + builder
│   ├── storage/       # SQLite DB + ArtifactStore (JSON files on disk)
│   ├── tools/         # Tool gateway (http, filesystem, shell, actions, mock_tools)
│   └── workspace/     # Workspace service (file operations, leases, knowledge base)
├── core_runtime/      # Thin execution wrapper (contracts, execute, eval_runner)
├── configs/           # System, routing, pod YAML configs
├── personas/          # Persona YAML definitions (7 personas)
├── config/personas/   # Runtime copy of personas (deployed identically)
├── evals/             # Smoke & replay evaluation packs
├── golden/            # Golden snapshot reference data
├── tests/             # 118 test files covering all core components
├── scripts/           # Dev/ops scripts (seed_pods, replay, regression, eval)
├── deployment/        # Abacus.AI deployment entry + Dockerfile
└── docker/            # Docker build file + docker-compose
```

---

## 2. Architecture Mapping — Bounded Execution System

### Component-by-Component Mapping

| Architecture Concept | Status | Implementation Location | Notes |
|---|---|---|---|
| **NOW Slice** | ✅ Implemented | `core/snapshot/schema.py`, `core/snapshot/builder.py`, `core/pod/pod.py::_build_now_slice_payload()` | Immutable execution context is constructed per-run. Contains request, policies, budgets, model rail, persona binding. Persisted to `now_slices` table and artifact store. |
| **Thread (Stateless Execution Unit)** | ✅ Implemented | `core/pod/pod.py::run()` | Each `Pod.run()` call is a stateless execution thread. It receives a Snapshot, produces output, is judged, and returns. No state leaks between runs. |
| **Persona / DNA (Behavior Contract)** | ✅ Implemented | `core/pod/dna.py`, `personas/*.yaml` | DNA dataclass defines persona, constraints, style, prompt template. 7 persona YAML files define specialized behaviors (implementation, reviewer, qa_test, clarifier, research, code_review, release_ops). |
| **Executor** | ✅ Implemented | `core/executor/runner.py`, `core/executor/prompts.py` | `ExecutorRunner` transforms NOW Slice + DNA → structured output. Has `MockBackend` (deterministic) and `OllamaBackend`/`RemoteBackend` for real LLM inference. Builds prompt from DNA constraints + snapshot context. |
| **Judge (External Evaluator)** | ✅ Implemented | `core/judge/judge.py`, `core/judge/validators.py` | Rule-based judge with 12+ validators: schema compliance, grounding, tool policy, intent matching, refusal detection, schema echo, hallucination, workflow completeness, QA quality. Produces pass/fail + scores + detailed failures. |
| **Attempt Loop (Retry Mechanism)** | ✅ Implemented | `core/pod/pod.py::run()` (lines within the `for attempt_num` loop) | Max 2 attempts per run. On failure, judge feedback is converted to a retry instruction via `_build_retry_message()`. Snapshot is cloned with retry context. Repair is tracked in lineage. |
| **Reward Firewall** | ✅ Implemented | `core/executor/sandbox.py` | `FORBIDDEN_SURVIVAL_PATTERNS` blocks 24+ patterns (survival, reward, score, fitness, selection, ranking, etc.). `detect_survival_awareness_violation()` scans all executor output. Violations create structured error records. |
| **Lineage** | ✅ Implemented | `core/pod/lineage.py`, `lineage_edges` DB table | Full DAG of parent→child edges with typed nodes (dna, artifact, run_attempt). Tracks: run_snapshot edges, repair_retry edges, mutation edges, seed edges. |
| **Attractors (Cross-run Memory)** | ✅ Implemented | `core/observability/traces.py`, `/v1/attractors` API endpoint | `behavior_fingerprint()` creates stable behavioral signatures from response structure. DB queries aggregate by behavior_fp to find recurring patterns. Attractor query API returns frequency, pass rates, repair rates. |
| **Commit Lane (Controlled Persistence)** | ⚠️ Partially Implemented | `apps/api/routes.py` (commit_propose, commit_curate endpoints) | Auto-commit and learn-mode paths exist in workflow orchestration. Commit propose/curate APIs are defined but the actual persistent knowledge integration loop is skeletal. |
| **DNA Mutation & Selection** | ✅ Implemented | `core/pod/mutation.py`, `core/pod/selection.py`, `core/pod/generator.py` | `mutate_dna()` toggles style knobs. `select_by_pass_rate()` picks best DNA by pass rate. `PodGenerator` spawns variant pods with config variations. Regression harness validates mutations against golden snapshots. |
| **Router** | ✅ Implemented | `core/router/router.py`, `core/router/bandit.py`, `core/router/classifier.py` | ε-greedy bandit routing (ε=0.15). Supports broadcast, weighted, and specialized modes. Per-request-type weight tracking. Signal pressure updates weights based on completion/retry/return/latency. |
| **Resource Allocator** | ✅ Implemented | `core/router/resources.py` | Traffic caps, compute budgets, incubation budgets for newly generated pods. Starvation detection kills underperforming pods. |
| **Workspace Service** | ✅ Implemented | `core/workspace/service.py` | Lease-based workspace access with capabilities (read/write/search). File I/O sandboxed to workspace directories. Knowledge base with doc commits and search. Content scanning for sensitive data. |
| **Dream Grid** | ✅ Implemented | `core/observability/dream_grid.py` | 10×10 boolean grid as behavioral fingerprint visualization. Analyzes density, entropy, symmetry, largest connected component. Used in judge result and UI display. |

---

## 3. What's Working

### Core Execution Pipeline (Fully Functional)
1. **Request → Snapshot → NOW Slice → Executor → Judge → Persist** — complete pipeline working end-to-end
2. **Attempt loop with repair** — failed runs get retried with judge feedback injected as context
3. **Multi-pod execution** — Router dispatches to multiple pods, picks winner by policy compliance + pass rate

### Workflow Orchestration (Working)
- Multi-step workflow engine in `routes.py::_execute_workflow_request()` (~3300 lines)
- Persona pipeline: research → clarifier → implementation → qa_test → release_ops
- Canonical targets: `hello_fastapi_service`, `service_bootstrap_app`
- Workspace creation, file generation, and test execution within workflows
- Clarification flow (ask user questions, resume with answers)
- Auto-commit and learn-mode paths

### Infrastructure (Working)
- **API:** 30+ REST endpoints covering all system operations
- **Database:** Full SQLite schema with 15+ tables, migration system
- **Artifact Store:** JSON-based artifact persistence with portable paths
- **Tests:** 118 tests covering: judge grounding, lineage tracking, routing, snapshots, tool gateway, dream grids, mutations, regression, fingerprints, reward firewall, workspace operations
- **MCP Server:** JSON-RPC stdio interface for IDE integration
- **UI:** React control surface with workflow execution, diagnostics, artifact inspector, attractor display

### Observable/Traceable
- Every run produces: trace fingerprint, behavior fingerprint, tool sequence fingerprint
- All artifacts (executor output, judge result, snapshots, NOW slices) persisted to disk + DB
- Full attempt history with per-attempt metrics

---

## 4. What's Missing / Incomplete

### High Priority

| Gap | Description | Impact |
|---|---|---|
| **Real LLM Integration** | `OllamaBackend` and `RemoteBackend` exist but the system defaults to `MockBackend` (hardcoded responses). No actual LLM inference happens without a running Ollama/OpenAI instance. | Core intelligence is simulated, not real |
| **True Reward Firewall Enforcement** | The firewall *detects* violations in executor output text, but there's no pre-execution firewall on inputs TO the executor. The executor could theoretically be prompted with reward-leaking context. | Partial protection — post-hoc only |
| **Commit Lane Depth** | Commit propose/curate endpoints exist but don't actually modify DNA registry, persona files, or playbooks on disk. The "learning" path aggregates data but doesn't produce durable system changes. | System can't truly evolve from experience |
| **Cross-Session Lineage** | Lineage is per-database-session. No mechanism to federate lineage across deployments or share attractor patterns between instances. | Limits emergent behavior to single instance |

### Medium Priority

| Gap | Description |
|---|---|
| **Planner Intelligence** | The planner in `_execute_workflow_request` uses hardcoded heuristics and keyword matching rather than LLM-based planning. |
| **Judge LLM Layer** | Judge is entirely rule-based. No LLM-as-judge capability for nuanced evaluation (e.g., code quality, semantic correctness). |
| **Persona Evolution** | Personas are static YAML files. No mechanism for personas to evolve based on accumulated attractor patterns. |
| **Multi-Model Support** | Router doesn't consider model diversity. All pods use the same model backend. No model-level A/B testing. |
| **External Signal Integration** | Signal ingest API exists but no real external sources (user feedback, A/B test results, production metrics) are connected. |

### Low Priority

| Gap | Description |
|---|---|
| **Authentication/Authorization** | No auth on any API endpoint. |
| **Rate Limiting** | No request throttling beyond the workflow lock. |
| **Horizontal Scaling** | SQLite + file-based artifacts don't scale beyond single node. |
| **UI Polish** | UI is functional but minimal — no real-time streaming, no graph visualization of lineage. |

---

## 5. What Was Removed / Cleaned Up

| File | Reason |
|---|---|
| `apps/ui/app.js` | Legacy vanilla JS UI — fully superseded by `src/main.jsx` (React 18). Contained identical functionality (workflow execution, dream grid rendering, artifact loading) but with DOM manipulation instead of React state. Also included an `AgenticVoice` class (Web Audio API ambient sound) that added unnecessary complexity. |
| `apps/ui/react-app.js` | Intermediate React implementation using `React.createElement()` calls and ESM imports from CDN. Superseded by the proper Vite-bundled `src/main.jsx` with JSX syntax and npm dependencies. |
| `docker/docker-compose.yml` | Duplicate of root `docker-compose.yml` with only path context changes (`..` vs `.`). The root file is the canonical one. |

### Noted but Retained (Duplication)
- `personas/` and `config/personas/` contain identical YAML files. The `config/personas/` directory is the runtime copy populated during setup/Docker build. Both are needed — `personas/` is the source of truth, `config/` is the deployed copy.
- `deployment/Dockerfile` vs `docker/Dockerfile` — these serve different purposes (Abacus deployment vs standard Docker).

---

## 6. How to Access the Running Services

### Backend (FastAPI)

- **VM localhost:** `http://localhost:8000`
- **Preview URL:** https://12bd60444e.na101.preview.abacusai.app
- **Health check:** `GET /health` → `{"status": "ok"}`
- **API prefix:** All endpoints under `/v1/`

### Frontend (React UI)

The frontend is built and served as static files by the FastAPI backend at the root path.

- **Same URLs as backend** — the UI is served at `/`
- **Preview URL:** https://12bd60444e.na101.preview.abacusai.app

### Key API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/v1/workflows` | POST | Execute multi-step workflow |
| `/v1/workflows/active` | GET | Check active workflow |
| `/v1/requests/{id}` | GET | Get request status + run details |
| `/v1/router` | POST | Route a request to persona |
| `/v1/submit` | POST | Submit simple request |
| `/v1/attractors` | GET | Query behavioral attractors |
| `/v1/artifacts/{id}` | GET | Load artifact by ID |
| `/v1/research` | POST | Run research query |
| `/v1/signals` | POST | Ingest external signal |
| `/v1/pods/generate` | POST | Generate new pod variants |
| `/v1/lineage` | GET | Query lineage graph |

---

## 7. Architecture Strengths

1. **Principled Bounded Execution** — The NOW Slice → Thread → Executor → Judge pattern is cleanly implemented. Each execution is genuinely stateless with immutable context.

2. **Comprehensive Traceability** — Every execution produces fingerprints (trace, behavior, tool sequence), all artifacts are persisted, lineage edges form a complete DAG. This is production-grade observability.

3. **Reward Firewall is Real** — 24+ forbidden patterns prevent survival awareness leakage. This is not theater — the patterns are specific and the enforcement creates structured error records.

4. **Test Coverage is Excellent** — 118 tests covering all core components. Tests validate fingerprint stability, firewall enforcement, lineage integrity, routing behavior, dream grid analysis, workspace isolation.

5. **Clean Separation of Concerns** — DNA defines behavior contract, Executor transforms, Judge evaluates, Router dispatches, Pod orchestrates. No component reaches into another's domain.

---

## 8. Next Steps for Building Out the System

### Phase 1: Real Intelligence (Immediate)
1. **Connect a real LLM backend** — Configure Ollama or OpenAI. The `OllamaBackend` and `RemoteBackend` already exist in `runner.py`. Set `MODEL_PROVIDER=openai` and `OPENAI_API_KEY=...` in `.env`.
2. **Add LLM-as-Judge layer** — Extend `Judge.evaluate()` to optionally invoke an LLM for semantic quality checks (code correctness, instruction following) beyond the current rule-based validators.

### Phase 2: True Evolution (Short-term)
3. **Implement Commit Lane** — Make `commit_curate` actually persist winning DNA configurations, update persona YAML files, and register new playbooks.
4. **Deepen mutation operators** — Current mutation only toggles verbosity. Add prompt template mutations, constraint additions/removals, and style variations.
5. **Connect external signals** — Wire user feedback (thumbs up/down), production error rates, and latency metrics into the signal pipeline to drive routing pressure.

### Phase 3: Emergent Behavior (Medium-term)
6. **Attractor-driven persona evolution** — Use accumulated attractor patterns to propose persona modifications. High-frequency successful behavior patterns should influence DNA.
7. **Cross-session lineage federation** — Persist lineage to a durable store (PostgreSQL) and share attractor summaries across deployment instances.
8. **Multi-model routing** — Extend the router to consider model backends as a routing dimension. Different pods could use different models optimized for their persona.

### Phase 4: Production Readiness (Longer-term)
9. **Authentication & authorization** — Add API key or OAuth-based auth.
10. **PostgreSQL migration** — Replace SQLite for concurrent access and horizontal scaling.
11. **Real-time UI** — Add WebSocket streaming for live execution progress and lineage graph visualization.
12. **Pre-execution reward firewall** — Scan inputs TO the executor, not just outputs FROM it, to prevent reward-context contamination upstream.

---

## 9. Test Summary

```
118 passed in 81.60s

Key test areas:
- test_judge_grounding.py          — Judge validates grounding claims
- test_lineage_tracking.py         — Lineage DAG integrity
- test_reward_firewall.py          — Survival awareness blocking
- test_now_slice_persistence.py    — NOW slice immutability
- test_snapshot_schema.py          — Snapshot schema validation
- test_weighted_routing_auto.py    — Routing weight updates
- test_dream_grid.py               — Dream grid analysis
- test_mutation_creates_new_dna.py — DNA mutation mechanics
- test_regression_harness.py       — Regression validation
- test_tool_gateway_budget.py      — Tool budget enforcement
- test_workspace_and_knowledge.py  — Workspace isolation
- test_e2e_hello_and_replay.py     — End-to-end workflow + replay
```

---

*This analysis reflects the state of the repository as of 2026-03-18. The system has a solid architectural foundation with most core bounded execution concepts implemented. The primary gap is connecting real LLM intelligence — the plumbing is all in place, waiting for a real model backend to bring the system to life.*
