const button = document.getElementById("submit");
const input = document.getElementById("input");
const actionMode = document.getElementById("action-mode");
const output = document.getElementById("output");
const voiceEnabled = document.getElementById("voice-enabled");
const disturbanceSlider = document.getElementById("disturbance");
const statusMeta = document.getElementById("status-meta");
const assistantText = document.getElementById("assistant-text");
const attemptList = document.getElementById("attempt-list");
const dreamGridWrap = document.getElementById("dream-grid-wrap");
const liveLog = document.getElementById("live-log");
const artifactIdInput = document.getElementById("artifact-id");
const loadArtifactButton = document.getElementById("load-artifact");
const plannerBrief = document.getElementById("planner-brief");
const plannerSummary = document.getElementById("planner-summary");
const plannerSections = document.getElementById("planner-sections");
const dreamSlots = [];
let logLines = [];

class AgenticVoice {
  constructor() {
    this.ctx = null;
    this.masterGain = null;
    this.humOsc = null;
    this.humGain = null;
    this.noiseSource = null;
    this.noiseGain = null;
    this.driftOsc = null;
    this.driftDepth = null;
    this.tremoloOsc = null;
    this.tremoloDepth = null;
    this.synth = window.speechSynthesis;
    this.baseHumHz = 110;
    this.baseHumGain = 0.018;
    this.baseNoiseGain = 0.004;
    this.disturbance = Number(disturbanceSlider?.value || 0.35);
  }

  async ensureStarted() {
    if (this.ctx) {
      if (this.ctx.state === "suspended") await this.ctx.resume();
      return;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    this.ctx = new Ctx();
    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.value = 0.9;
    this.masterGain.connect(this.ctx.destination);

    this.humOsc = this.ctx.createOscillator();
    this.humOsc.type = "sine";
    this.humOsc.frequency.value = this.baseHumHz;
    this.humGain = this.ctx.createGain();
    this.humGain.gain.value = this.baseHumGain;
    this.humOsc.connect(this.humGain).connect(this.masterGain);

    this.driftOsc = this.ctx.createOscillator();
    this.driftOsc.type = "sine";
    this.driftOsc.frequency.value = 0.18;
    this.driftDepth = this.ctx.createGain();
    this.driftDepth.gain.value = 0.9;
    this.driftOsc.connect(this.driftDepth).connect(this.humOsc.frequency);

    this.tremoloOsc = this.ctx.createOscillator();
    this.tremoloOsc.type = "sine";
    this.tremoloOsc.frequency.value = 8.0;
    this.tremoloDepth = this.ctx.createGain();
    this.tremoloDepth.gain.value = 0.0;
    this.tremoloOsc.connect(this.tremoloDepth).connect(this.humGain.gain);

    const noiseBuffer = this.ctx.createBuffer(1, this.ctx.sampleRate * 2, this.ctx.sampleRate);
    const channel = noiseBuffer.getChannelData(0);
    for (let i = 0; i < channel.length; i += 1) {
      channel[i] = Math.random() * 2 - 1;
    }
    this.noiseSource = this.ctx.createBufferSource();
    this.noiseSource.buffer = noiseBuffer;
    this.noiseSource.loop = true;
    const noiseFilter = this.ctx.createBiquadFilter();
    noiseFilter.type = "lowpass";
    noiseFilter.frequency.value = 2400;
    this.noiseGain = this.ctx.createGain();
    this.noiseGain.gain.value = this.baseNoiseGain;
    this.noiseSource.connect(noiseFilter).connect(this.noiseGain).connect(this.masterGain);

    this.humOsc.start();
    this.driftOsc.start();
    this.tremoloOsc.start();
    this.noiseSource.start();
    this.updateDisturbance(this.disturbance);
  }

  updateDisturbance(v) {
    this.disturbance = Math.max(0, Math.min(1, Number(v)));
    if (!this.ctx || !this.humGain || !this.noiseGain || !this.driftDepth) return;
    const now = this.ctx.currentTime;
    const targetHum = this.baseHumGain * (0.7 + this.disturbance * 0.8);
    const targetNoise = this.baseNoiseGain * (0.6 + this.disturbance * 2.0);
    const targetDrift = 0.5 + this.disturbance * 1.5;
    this.humGain.gain.setTargetAtTime(targetHum, now, 0.08);
    this.noiseGain.gain.setTargetAtTime(targetNoise, now, 0.08);
    this.driftDepth.gain.setTargetAtTime(targetDrift, now, 0.08);
  }

  duck(on) {
    if (!this.ctx || !this.humGain || !this.tremoloDepth) return;
    const now = this.ctx.currentTime;
    const duckHum = this.baseHumGain * 0.42;
    const normalHum = this.baseHumGain * (0.7 + this.disturbance * 0.8);
    this.humGain.gain.cancelScheduledValues(now);
    this.humGain.gain.setTargetAtTime(on ? duckHum : normalHum, now, on ? 0.03 : 0.15);
    this.tremoloDepth.gain.cancelScheduledValues(now);
    this.tremoloDepth.gain.setTargetAtTime(on ? 0.0025 : 0.0, now, on ? 0.02 : 0.12);
  }

  async speak(text) {
    if (!text || !this.synth || !voiceEnabled.checked) return;
    await this.ensureStarted();
    this.synth.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.rate = 1.0;
    utt.pitch = 0.95;
    utt.onstart = () => this.duck(true);
    utt.onend = () => this.duck(false);
    utt.onerror = () => this.duck(false);
    this.synth.speak(utt);
  }

  stopSpeech() {
    if (this.synth) this.synth.cancel();
    this.duck(false);
  }
}

const agenticVoice = new AgenticVoice();

function chip(label, kind = "") {
  const cls = kind ? `chip ${kind}` : "chip";
  return `<span class="${cls}">${label}</span>`;
}

function normalizeAssistantContent(raw) {
  if (typeof raw !== "string") return "";
  const text = raw.trim();
  if (!text) return "";

  // Common schema-echo markdown: **type: final** **content: ...**
  const mdMatch = text.match(/\*\*content:\s*([\s\S]*?)\*\*$/i);
  if (mdMatch && mdMatch[1]) return mdMatch[1].trim();

  // Plain schema-echo text: type: final content: ...
  const plainMatch = text.match(/(?:^|\s)content:\s*([\s\S]*)$/i);
  if (plainMatch && plainMatch[1]) return plainMatch[1].trim();

  // JSON-ish echo: {"type":"final","content":"..."}
  if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("```") && text.includes("content"))) {
    const stripped = text.replace(/^```(?:json)?/i, "").replace(/```$/, "").trim();
    try {
      const parsed = JSON.parse(stripped);
      if (parsed && typeof parsed.content === "string") return parsed.content.trim();
    } catch {
      // Keep original text if parsing fails.
    }
  }
  return text;
}

function speakTextFromPayload(payload) {
  const content = payload?.result?.executor_output?.response?.content;
  return normalizeAssistantContent(content);
}

function renderStatus(payload) {
  const status = String(payload?.status || "unknown");
  const runId = String(payload?.winner_run_id || payload?.chosen_run_id || payload?.final_winner_run_id || "-");
  const requestId = String(payload?.request_id || payload?.workflow_id || "-");
  const dnaId = String(payload?.dna_id || "-");
  const ok = status === "completed" || Boolean(payload?.final_pass);

  statusMeta.innerHTML = [
    chip(ok ? "completed" : status, ok ? "ok" : "fail"),
    chip(`request ${requestId.slice(-8)}`),
    chip(`run ${runId.slice(-8)}`),
    chip(`dna ${dnaId.slice(-8)}`),
  ].join("");
}

function renderSimpleStatus(kind, payload) {
  const id =
    payload?.artifact_id ||
    payload?.workflow_graph_artifact_id ||
    payload?.request_id ||
    payload?.workflow_id ||
    "-";
  statusMeta.innerHTML = [chip(kind, "ok"), chip(`id ${String(id).slice(-8)}`)].join("");
}

function metric(label, value) {
  return chip(`${label}: ${value}`);
}

function resetLiveLog() {
  logLines = [];
  if (liveLog) liveLog.textContent = "";
}

function appendLiveLog(line) {
  const ts = new Date().toLocaleTimeString();
  logLines.push(`[${ts}] ${line}`);
  if (logLines.length > 200) logLines = logLines.slice(logLines.length - 200);
  if (liveLog) {
    liveLog.textContent = logLines.join("\n");
    liveLog.scrollTop = liveLog.scrollHeight;
  }
}

function shortId(value) {
  const s = String(value || "-");
  return s.length > 8 ? s.slice(-8) : s;
}

function logWorkflowSummary(workflowPayload) {
  if (!workflowPayload || typeof workflowPayload !== "object") return;
  const workflowId = shortId(workflowPayload.workflow_id);
  const requestId = shortId(workflowPayload.request_id);
  const target = String(workflowPayload.canonical_target || "none");
  appendLiveLog(`workflow ${workflowId} request=${requestId} target=${target}`);
  if (workflowPayload.planner_artifact_id) {
    appendLiveLog(`planner artifact=${shortId(workflowPayload.planner_artifact_id)}`);
  }
  if (workflowPayload.decomposition_plan_artifact_id) {
    appendLiveLog(`decomposition artifact=${shortId(workflowPayload.decomposition_plan_artifact_id)}`);
  }
  const steps = Array.isArray(workflowPayload.steps) ? workflowPayload.steps : [];
  steps.forEach((step, idx) => {
    appendLiveLog(
      `step ${idx + 1} persona=${String(step?.persona_id || "general")} ${step?.pass ? "pass" : "fail"} run=${shortId(step?.run_id)}`,
    );
  });
  const spawn = workflowPayload.spawn;
  if (spawn && typeof spawn === "object" && spawn.attempted) {
    appendLiveLog(
      `spawn pod=${shortId(spawn.spawned_pod_id)} child_workflow=${shortId(spawn.child_workflow_id)} child_pass=${Boolean(spawn.child_final_pass)}`,
    );
  }
}

function renderAttempts(runDetails) {
  attemptList.innerHTML = "";
  const runs = Array.isArray(runDetails) ? runDetails : [];
  if (!runs.length) {
    attemptList.innerHTML = `<div class="attempt"><small>No run details available.</small></div>`;
    return;
  }

  const allAttempts = [];
  runs.forEach((run) => {
    const attempts = Array.isArray(run.attempts) ? run.attempts : [];
    if (!attempts.length) {
      attemptList.innerHTML += `<div class="attempt"><small>Run ${run.run_id}: attempts hidden.</small></div>`;
      return;
    }
    attempts.forEach((att, idx) => {
      const pass = Boolean(att.pass);
      const delay = `${idx * 70}ms`;
      const attemptHtml = `
        <article class="attempt" style="animation-delay:${delay}">
          <div class="attempt-header">
            <strong>Attempt ${att.attempt_num}</strong>
            <small>${pass ? "pass" : "fail"} · ${att.latency_ms}ms · ${String(att.persona_id || "general")}</small>
          </div>
          <div class="attempt-metrics">
            ${metric("persona", att.persona_id || "general")}
            ${metric("pver", String(att.persona_version || "pv_unknown").slice(-8))}
            ${metric("handoff", att.handoff_artifact_id ? String(att.handoff_artifact_id).slice(-8) : "none")}
            ${metric("dream", att.dream_grid_fp || "n/a")}
            ${metric("density", Number(att.dream_density || 0).toFixed(2))}
            ${metric("entropy", Number(att.dream_entropy || 0).toFixed(2))}
            ${metric("largest", att.dream_largest_component || 0)}
            ${metric("sym", Number(att.dream_symmetry || 0).toFixed(2))}
          </div>
        </article>`;
      attemptList.innerHTML += attemptHtml;
      allAttempts.push(att);

      if (Array.isArray(att.dream_grid) && att.dream_grid.length === 10) {
        // Rendered through persistent dream slot morphing below.
      }
    });
  });
  renderDreamSlots(allAttempts);
}

function renderWorkflowSteps(workflow) {
  const steps = Array.isArray(workflow?.steps) ? workflow.steps : [];
  if (!steps.length) return;
  attemptList.innerHTML = "";
  steps.forEach((step, idx) => {
    const pass = Boolean(step.pass);
    const stepHtml = `
      <article class="attempt" style="animation-delay:${idx * 70}ms">
        <div class="attempt-header">
          <strong>Workflow Step ${idx + 1}</strong>
          <small>${pass ? "pass" : "fail"} · ${String(step.persona_id || "general")}</small>
        </div>
        <div class="attempt-metrics">
          ${metric("run", String(step.run_id || "-").slice(-8))}
          ${metric("attempt", String(step.attempt_id || "-").slice(-8))}
          ${metric("persona", step.persona_id || "general")}
          ${metric("pver", String(step.persona_version || "pv_unknown").slice(-8))}
          ${metric("handoff", step.handoff_artifact_id ? String(step.handoff_artifact_id).slice(-8) : "none")}
        </div>
      </article>`;
    attemptList.innerHTML += stepHtml;
  });
}

function plannerListSection(title, items, formatter = null) {
  if (!Array.isArray(items) || !items.length) return "";
  const lis = items
    .map((item) => {
      if (formatter) return `<li>${formatter(item)}</li>`;
      return `<li>${String(item)}</li>`;
    })
    .join("");
  return `<div class="planner-section"><strong>${title}</strong><ul>${lis}</ul></div>`;
}

function renderPlannerBrief(planner, canonicalTarget) {
  if (!plannerBrief || !plannerSummary || !plannerSections) return;
  if (!planner || typeof planner !== "object") {
    plannerBrief.style.display = "none";
    plannerSummary.innerHTML = "";
    plannerSections.innerHTML = "";
    return;
  }
  const target = String(canonicalTarget || planner.canonical_target || "none");
  const confidence = Number(planner.confidence || 0);
  const reasons = Array.isArray(planner.reason_tags) ? planner.reason_tags : [];
  plannerSummary.innerHTML = [
    chip(`target ${target}`),
    chip(`confidence ${confidence.toFixed(2)}`),
    reasons.length ? chip(`reasons ${reasons.join(",")}`) : "",
  ]
    .filter(Boolean)
    .join("");

  const components = Array.isArray(planner.components) ? planner.components : [];
  const assumptions = Array.isArray(planner.assumptions) ? planner.assumptions : [];
  const confirmations = Array.isArray(planner.confirmations_needed) ? planner.confirmations_needed : [];
  const steps = Array.isArray(planner.plan_steps) ? planner.plan_steps : [];
  const risks = Array.isArray(planner.risk_flags) ? planner.risk_flags : [];

  plannerSections.innerHTML = [
    plannerListSection("Components", components, (c) => {
      if (!c || typeof c !== "object") return String(c);
      const required = c.required ? "required" : "optional";
      return `<code>${String(c.name || c.id || "component")}</code> (${required}) - ${String(c.details || "")}`;
    }),
    plannerListSection("Assumptions", assumptions),
    plannerListSection("Confirmations Needed", confirmations),
    plannerListSection("Plan Steps", steps),
    plannerListSection("Risk Flags", risks, (r) => `<code>${String(r)}</code>`),
  ]
    .filter(Boolean)
    .join("");
  plannerBrief.style.display = "";
}

async function loadArtifactById(artifactId) {
  const id = String(artifactId || "").trim();
  if (!id) return;
  const res = await fetch(`/v1/artifacts/${encodeURIComponent(id)}`);
  if (!res.ok) {
    throw new Error(`artifact lookup failed: ${res.status}`);
  }
  const payload = await res.json();
  output.textContent = JSON.stringify(payload, null, 2);
}

function ensureDreamSlot(index) {
  if (dreamSlots[index]) return dreamSlots[index];
  const card = document.createElement("article");
  card.className = "dream-card";
  const title = document.createElement("h3");
  const grid = document.createElement("div");
  grid.className = "dream-grid";
  const stats = document.createElement("div");
  stats.className = "dream-stats";
  const cells = [];
  for (let i = 0; i < 100; i += 1) {
    const cell = document.createElement("div");
    cell.className = "dream-cell";
    grid.appendChild(cell);
    cells.push(cell);
  }
  card.appendChild(title);
  card.appendChild(grid);
  card.appendChild(stats);
  dreamGridWrap.appendChild(card);
  const slot = { card, title, stats, cells, data: null };
  dreamSlots[index] = slot;
  return slot;
}

function flattenGrid(grid) {
  if (!Array.isArray(grid) || grid.length !== 10) return null;
  const flat = [];
  for (let r = 0; r < 10; r += 1) {
    if (!Array.isArray(grid[r]) || grid[r].length !== 10) return null;
    for (let c = 0; c < 10; c += 1) {
      flat.push(Number(grid[r][c]) === 1 ? 1 : 0);
    }
  }
  return flat;
}

function renderDreamSlots(attempts) {
  const withGrid = attempts.filter((att) => Array.isArray(att.dream_grid) && att.dream_grid.length === 10);
  withGrid.forEach((att, index) => {
    const slot = ensureDreamSlot(index);
    const nextGrid = flattenGrid(att.dream_grid);
    slot.card.style.display = "";
    slot.card.classList.remove("loading");
    slot.title.textContent = `Attempt ${att.attempt_num} · ${att.dream_grid_fp || "n/a"}`;
    slot.stats.innerHTML = [
      metric("popcount", att.dream_popcount || 0),
      metric("density", Number(att.dream_density || 0).toFixed(2)),
      metric("entropy", Number(att.dream_entropy || 0).toFixed(2)),
    ].join("");
    if (!nextGrid) return;
    for (let i = 0; i < slot.cells.length; i += 1) {
      const prev = slot.data ? slot.data[i] : null;
      const next = nextGrid[i];
      if (prev !== next) {
        slot.cells[i].classList.toggle("on", next === 1);
        slot.cells[i].style.transform = "scale(0.86)";
        slot.cells[i].style.transitionDelay = `${(i % 10) * 12}ms`;
        requestAnimationFrame(() => {
          slot.cells[i].style.transform = "scale(1)";
        });
      }
    }
    slot.data = nextGrid;
  });
  for (let i = withGrid.length; i < dreamSlots.length; i += 1) {
    if (!dreamSlots[i]) continue;
    dreamSlots[i].card.style.display = "none";
  }
}

function setDreamLoading(isLoading) {
  dreamSlots.forEach((slot) => {
    if (!slot || slot.card.style.display === "none") return;
    slot.card.classList.toggle("loading", isLoading);
  });
}

async function fetchRequestStatus(requestId) {
  const seenAttempts = new Set();
  const seenTools = new Set();
  let lastStatus = "";
  const maxPolls = 600; // ~5 minutes at 500ms
  for (let i = 0; i < maxPolls; i += 1) {
    const res = await fetch(
      `/v1/requests/${encodeURIComponent(requestId)}?include_run_details=1&include_attempts=1`,
    );
    const payload = await res.json();
    renderStatus(payload);
    const status = String(payload?.status || "unknown");
    if (status !== lastStatus) {
      appendLiveLog(`request ${requestId.slice(-8)} status=${status}`);
      lastStatus = status;
    }
    const runDetails = Array.isArray(payload?.run_details) ? payload.run_details : [];
    runDetails.forEach((run) => {
      const runId = String(run?.run_id || "");
      const attempts = Array.isArray(run?.attempts) ? run.attempts : [];
      attempts.forEach((att) => {
        const attId = String(att?.attempt_id || "");
        if (!attId || seenAttempts.has(attId)) return;
        seenAttempts.add(attId);
        const pass = Boolean(att?.pass);
        appendLiveLog(
          `run ${runId.slice(-8)} attempt ${att?.attempt_num || "?"} persona=${att?.persona_id || "general"} ${pass ? "pass" : "fail"} latency=${att?.latency_ms || 0}ms`,
        );
      });
      const tools = Array.isArray(run?.tool_calls) ? run.tool_calls : [];
      tools.forEach((tool) => {
        const tcId = String(tool?.tool_call_id || "");
        if (!tcId || seenTools.has(tcId)) return;
        seenTools.add(tcId);
        const allowed = Boolean(tool?.allowed);
        appendLiveLog(
          `tool ${tool?.tool || "unknown"} ${allowed ? "allowed" : "blocked"}${tool?.blocked_reason ? ` (${tool.blocked_reason})` : ""}`,
        );
      });
    });
    const terminal = new Set(["completed", "done", "success", "failed", "blocked", "error"]);
    if (terminal.has(String(payload?.status || "").toLowerCase())) return payload;
    if (i % 10 === 0) {
      assistantText.textContent = `Running... (${Math.floor((i * 500) / 1000)}s elapsed)`;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("timed out waiting for completion after 300s; check /v1/requests/<request_id> and docker logs");
}

async function runAction(mode, userInput) {
  if (mode === "research") {
    const res = await fetch("/v1/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: userInput, max_hits: 20 }),
    });
    if (!res.ok) throw new Error(`research failed: ${res.status}`);
    const payload = await res.json();
    renderSimpleStatus("research", payload);
    return { mode, payload, status: null };
  }
  if (mode === "router") {
    const res = await fetch("/v1/router", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_input: userInput,
        allowed_personas: ["research", "code_review", "implementation", "qa_test", "release_ops", "general", "clarifier"],
      }),
    });
    if (!res.ok) throw new Error(`router failed: ${res.status}`);
    const payload = await res.json();
    renderSimpleStatus("router", payload);
    return { mode, payload, status: null };
  }
  const workflowBody = {
    user_input: userInput,
    request_type: mode === "hello_service" || mode === "service_bootstrap" ? "coding" : "auto",
    max_steps: mode === "hello_service" || mode === "service_bootstrap" ? 6 : 4,
    retry_same_persona_once: true,
    learn_mode: true,
  };
  if (mode === "hello_service") {
    workflowBody.canonical_target = "hello_fastapi_service";
  } else if (mode === "service_bootstrap") {
    workflowBody.canonical_target = "service_bootstrap_app";
  }
  const submitRes = await fetch("/v1/workflows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(workflowBody),
  });
  if (!submitRes.ok) {
    throw new Error(`workflow failed: ${submitRes.status}`);
  }
    const workflowPayload = await submitRes.json();
    logWorkflowSummary(workflowPayload);
    const status = workflowPayload?.request_id
      ? await fetchRequestStatus(workflowPayload.request_id)
      : null;
  return { mode, payload: workflowPayload, status };
}

button.addEventListener("click", async () => {
  const userInput = input.value.trim();
  if (!userInput) return;
  const mode = String(actionMode?.value || "workflow");
  button.disabled = true;
  button.textContent = "Running...";
  try {
    resetLiveLog();
    if (voiceEnabled.checked) await agenticVoice.ensureStarted();
    agenticVoice.stopSpeech();
    assistantText.textContent = "Running request...";
    appendLiveLog(`mode=${mode} started`);
    attemptList.innerHTML = "";
    setDreamLoading(true);
    const result = await runAction(mode, userInput);
    appendLiveLog(`mode=${mode} request completed`);
    if (artifactIdInput && result?.payload?.workflow_graph_artifact_id) {
      artifactIdInput.value = String(result.payload.workflow_graph_artifact_id);
      appendLiveLog(`workflow graph artifact=${String(result.payload.workflow_graph_artifact_id).slice(-8)}`);
    } else if (artifactIdInput && result?.payload?.artifact_id) {
      artifactIdInput.value = String(result.payload.artifact_id);
      appendLiveLog(`artifact=${String(result.payload.artifact_id).slice(-8)}`);
    }
    output.textContent = JSON.stringify(result, null, 2);

    let speechText = "";
    if (mode === "research") {
      renderPlannerBrief(null, "");
      speechText = String(result.payload?.report?.summary || "Research completed.");
      assistantText.textContent = speechText;
      appendLiveLog(`research summary: ${speechText}`);
      dreamSlots.forEach((slot) => {
        if (slot) slot.card.style.display = "none";
      });
    } else if (mode === "router") {
      renderPlannerBrief(null, "");
      const persona = String(result.payload?.selected_persona_id || "general");
      const reasons = (result.payload?.reason_tags || []).join(", ");
      speechText = `Router selected ${persona}${reasons ? ` with ${reasons}` : ""}.`;
      assistantText.textContent = speechText;
      appendLiveLog(`router selected persona=${persona}${reasons ? ` reasons=${reasons}` : ""}`);
      dreamSlots.forEach((slot) => {
        if (slot) slot.card.style.display = "none";
      });
    } else {
      const workflowPayload = result.payload;
      const status = result.status;
      const combined = { workflow: workflowPayload, status };
      output.textContent = JSON.stringify(combined, null, 2);
      speechText = speakTextFromPayload(status || workflowPayload);
      assistantText.textContent = speechText || "No assistant text returned.";
      const inferredTarget = String(workflowPayload?.canonical_target || "").trim();
      renderPlannerBrief(workflowPayload?.planner || null, inferredTarget);
      if (inferredTarget) {
        appendLiveLog(`planner target=${inferredTarget}`);
      }
      if (workflowPayload?.service_hello_url) {
        appendLiveLog(`service endpoint ${workflowPayload.service_hello_url}`);
      } else if (workflowPayload?.service_url) {
        appendLiveLog(`service base ${workflowPayload.service_url}`);
      }
      const autoCommit = workflowPayload?.auto_commit;
      if (autoCommit?.attempted) {
        appendLiveLog(
          `auto-commit ${autoCommit?.pass ? "passed" : "failed"}${
            autoCommit?.commit_artifact_id ? ` commit=${String(autoCommit.commit_artifact_id).slice(-8)}` : ""
          }`,
        );
      } else if (autoCommit?.reason) {
        appendLiveLog(`auto-commit skipped (${String(autoCommit.reason)})`);
      }
      const learning = workflowPayload?.learning;
      if (learning?.attempted) {
        appendLiveLog(
          `learn-mode ${learning?.pass ? "committed" : "no-commit"} count=${Number(learning?.committed_count || 0)}`,
        );
      }
      appendLiveLog(
        `workflow final status=${String(workflowPayload?.final_status || "-")} final_pass=${Boolean(workflowPayload?.final_pass)} run=${shortId(workflowPayload?.final_winner_run_id)}`,
      );
      if (status?.run_details) {
        renderAttempts(status.run_details);
      } else {
        renderWorkflowSteps(workflowPayload);
      }
    }
    setDreamLoading(false);
    if (speechText) await agenticVoice.speak(speechText);
  } catch (err) {
    assistantText.textContent = String(err);
    appendLiveLog(`error: ${String(err)}`);
    setDreamLoading(false);
  } finally {
    button.disabled = false;
    button.textContent = "Run";
  }
});

voiceEnabled.addEventListener("change", async () => {
  if (voiceEnabled.checked) {
    await agenticVoice.ensureStarted();
  } else {
    agenticVoice.stopSpeech();
  }
});

disturbanceSlider.addEventListener("input", (event) => {
  agenticVoice.updateDisturbance(event.target.value);
});

if (loadArtifactButton) {
  loadArtifactButton.addEventListener("click", async () => {
    try {
      await loadArtifactById(artifactIdInput?.value || "");
    } catch (err) {
      assistantText.textContent = String(err);
    }
  });
}
