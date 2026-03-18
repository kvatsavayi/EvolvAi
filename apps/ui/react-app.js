import React, { useEffect, useMemo, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";

const h = React.createElement;

const DEFAULT_LAYOUT = {
  main: ["status", "timeline", "planner"],
  sidebar: ["service", "active"],
  bottom: ["log", "raw"],
};

const WIDGETS = new Set(["status", "timeline", "planner", "service", "active", "log", "raw"]);
const LAYOUT_KEY = "ops_console_layout_v1";

function loadLayout() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "");
    if (parsed && typeof parsed === "object") return parsed;
  } catch {
    // ignore malformed value
  }
  return DEFAULT_LAYOUT;
}

function saveLayout(layout) {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
}

function cloneLayout(layout) {
  return {
    main: Array.isArray(layout?.main) ? [...layout.main] : [],
    sidebar: Array.isArray(layout?.sidebar) ? [...layout.sidebar] : [],
    bottom: Array.isArray(layout?.bottom) ? [...layout.bottom] : [],
  };
}

function normalizeLayout(layout) {
  const clean = cloneLayout(layout);
  const seen = new Set();
  for (const zone of ["main", "sidebar", "bottom"]) {
    clean[zone] = clean[zone].filter((id) => {
      if (!WIDGETS.has(id)) return false;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }
  for (const id of WIDGETS) {
    if (!seen.has(id)) clean.main.push(id);
  }
  return clean;
}

function applyLayoutCommand(input, layout) {
  const text = String(input || "").trim().toLowerCase();
  const move = text.match(/^move\s+([a-z_\-\s]+)\s+to\s+(main|sidebar|bottom)$/i);
  const show = text.match(/^show\s+([a-z_\-\s]+)$/i);
  const hide = text.match(/^hide\s+([a-z_\-\s]+)$/i);
  if (!move && !show && !hide) return { matched: false, layout, note: "" };

  const alias = (raw) => {
    const key = String(raw || "").trim().replace(/\s+/g, "_");
    const map = {
      dashboard: "status",
      status: "status",
      timeline: "timeline",
      attempts: "timeline",
      planner: "planner",
      service: "service",
      active: "active",
      log: "log",
      logs: "log",
      raw: "raw",
      payload: "raw",
    };
    return map[key] || key;
  };

  const next = normalizeLayout(cloneLayout(layout));
  const removeEverywhere = (item) => {
    for (const zone of ["main", "sidebar", "bottom"]) {
      next[zone] = next[zone].filter((x) => x !== item);
    }
  };

  if (move) {
    const item = alias(move[1]);
    const zone = move[2];
    if (!WIDGETS.has(item)) return { matched: true, layout, note: `Unknown item: ${item}` };
    removeEverywhere(item);
    next[zone].push(item);
    return { matched: true, layout: normalizeLayout(next), note: `Moved ${item} to ${zone}.` };
  }
  if (show) {
    const item = alias(show[1]);
    if (!WIDGETS.has(item)) return { matched: true, layout, note: `Unknown item: ${item}` };
    removeEverywhere(item);
    next.main.push(item);
    return { matched: true, layout: normalizeLayout(next), note: `Showing ${item} in main.` };
  }
  if (hide) {
    const item = alias(hide[1]);
    if (!WIDGETS.has(item)) return { matched: true, layout, note: `Unknown item: ${item}` };
    removeEverywhere(item);
    return { matched: true, layout: normalizeLayout(next), note: `Hid ${item}.` };
  }
  return { matched: false, layout, note: "" };
}

function chip(text, tone = "") {
  return h("span", { className: `chip ${tone}`.trim() }, text);
}

function panel(title, content, key) {
  return h(
    "section",
    { className: "panel", key },
    h("h2", null, title),
    content,
  );
}

function App() {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState("workflow");
  const [running, setRunning] = useState(false);
  const [workflow, setWorkflow] = useState(null);
  const [activeWorkflow, setActiveWorkflow] = useState(null);
  const [artifactId, setArtifactId] = useState("");
  const [artifactData, setArtifactData] = useState(null);
  const [layout, setLayout] = useState(() => normalizeLayout(loadLayout()));
  const [logs, setLogs] = useState([]);

  const appendLog = (line) => {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev.slice(-300), `[${ts}] ${line}`]);
  };

  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const res = await fetch("/v1/workflows/active");
        const payload = await res.json();
        setActiveWorkflow(payload.active ? payload.workflow : null);
      } catch {
        // no-op
      }
    }, 2000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    saveLayout(layout);
  }, [layout]);

  const summary = useMemo(() => {
    if (!workflow) return null;
    const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
    return {
      target: workflow.canonical_target || "n/a",
      workflowId: workflow.workflow_id || "n/a",
      requestId: workflow.request_id || "n/a",
      finalStatus: workflow.final_status || "n/a",
      finalPass: Boolean(workflow.final_pass),
      steps,
      passCount: steps.filter((s) => s.pass).length,
      serviceUrl: workflow.service_hello_url || workflow.service_url || "",
    };
  }, [workflow]);

  async function runPrompt() {
    if (!prompt.trim()) return;

    const cmd = applyLayoutCommand(prompt, layout);
    if (cmd.matched) {
      setLayout(cmd.layout);
      appendLog(cmd.note);
      return;
    }

    if (running || activeWorkflow) {
      appendLog("workflow blocked: one workflow already running");
      return;
    }

    setRunning(true);
    setWorkflow(null);
    appendLog(`mode=${mode} started`);
    try {
      if (mode === "research") {
        const res = await fetch("/v1/research", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: prompt, max_hits: 20 }),
        });
        const payload = await res.json();
        setWorkflow({
          workflow_id: payload.research_report_id,
          request_id: payload.run_id || "research",
          canonical_target: "research",
          final_status: "done",
          final_pass: true,
          steps: [],
          raw: payload,
        });
        appendLog(`research done report=${payload.research_report_id}`);
        return;
      }

      const body = {
        user_input: prompt,
        request_type: mode === "hello_service" || mode === "service_bootstrap" ? "coding" : "auto",
        max_steps: mode === "hello_service" || mode === "service_bootstrap" ? 8 : 6,
        retry_same_persona_once: true,
        learn_mode: true,
      };
      if (mode === "hello_service") body.canonical_target = "hello_fastapi_service";
      if (mode === "service_bootstrap") body.canonical_target = "service_bootstrap_app";

      const res = await fetch("/v1/workflows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await res.json();
      if (!res.ok) {
        appendLog(`workflow error ${res.status}: ${JSON.stringify(payload)}`);
        return;
      }
      setWorkflow(payload);
      if (payload.workflow_graph_artifact_id) setArtifactId(payload.workflow_graph_artifact_id);
      appendLog(`workflow ${payload.workflow_id} target=${payload.canonical_target || "n/a"}`);
      appendLog(`final=${payload.final_status} pass=${Boolean(payload.final_pass)}`);
      if (payload.service_hello_url) appendLog(`service endpoint ${payload.service_hello_url}`);
    } catch (err) {
      appendLog(String(err));
    } finally {
      setRunning(false);
    }
  }

  async function loadArtifact() {
    const id = artifactId.trim();
    if (!id) return;
    try {
      const res = await fetch(`/v1/artifacts/${encodeURIComponent(id)}`);
      const payload = await res.json();
      if (!res.ok) throw new Error(JSON.stringify(payload));
      setArtifactData(payload);
      appendLog(`loaded artifact ${id}`);
    } catch (err) {
      appendLog(`artifact load error: ${String(err)}`);
    }
  }

  const widget = (id) => {
    if (id === "status") {
      return panel(
        "Workflow Status",
        h(
          React.Fragment,
          null,
          h(
            "div",
            { className: "chips-row" },
            chip(running ? "running" : "idle", running ? "warn" : "ok"),
            summary ? chip(`target ${summary.target}`) : null,
            summary ? chip(`steps ${summary.steps.length}`) : null,
            summary ? chip(summary.finalPass ? "pass" : "fail", summary.finalPass ? "ok" : "fail") : null,
          ),
          h(
            "p",
            { className: "assistant-text" },
            summary
              ? `workflow ${summary.workflowId} (${summary.passCount}/${summary.steps.length} steps passed)`
              : "Run a workflow or type a layout command like: move timeline to sidebar",
          ),
        ),
        id,
      );
    }

    if (id === "timeline") {
      const steps = summary?.steps || [];
      return panel(
        "Timeline",
        h(
          "div",
          { className: "attempt-list" },
          steps.length
            ? steps.map((s, idx) =>
                h(
                  "article",
                  { className: "attempt", key: `${s.run_id || "run"}-${idx}` },
                  h(
                    "div",
                    { className: "attempt-header" },
                    h("strong", null, `step ${idx + 1}: ${String(s.persona_id || "general")}`),
                    h("small", null, s.pass ? "pass" : "fail"),
                  ),
                  h(
                    "div",
                    { className: "attempt-metrics" },
                    chip(`run ${String(s.run_id || "-").slice(-8)}`),
                    chip(`attempt ${String(s.attempt_id || "-").slice(-8)}`),
                  ),
                ),
              )
            : h("p", { className: "muted" }, "No steps yet."),
        ),
        id,
      );
    }

    if (id === "planner") {
      const planner = workflow?.planner || {};
      const tags = Array.isArray(planner.reason_tags) ? planner.reason_tags : [];
      return panel(
        "Planner",
        h(
          "div",
          { className: "planner-brief", style: { marginTop: 0 } },
          h(
            "div",
            { className: "planner-summary" },
            chip(`target ${String(planner.canonical_target || "n/a")}`),
            chip(`confidence ${String(planner.confidence ?? "n/a")}`),
            tags.length ? chip(`reasons ${tags.join(",")}`) : null,
          ),
          h(
            "div",
            { className: "planner-sections" },
            h("div", { className: "planner-section" }, h("strong", null, "Plan"), h("ul", null, ...(Array.isArray(planner.plan_steps) ? planner.plan_steps : []).slice(0, 6).map((x, i) => h("li", { key: i }, String(x))))),
          ),
        ),
        id,
      );
    }

    if (id === "service") {
      return panel(
        "Service",
        h(
          "div",
          null,
          summary?.serviceUrl
            ? h("p", { className: "mono" }, summary.serviceUrl)
            : h("p", { className: "muted" }, "No live service URL from latest workflow."),
        ),
        id,
      );
    }

    if (id === "active") {
      return panel(
        "Active Workflow",
        activeWorkflow
          ? h(
              "div",
              null,
              h("p", { className: "mono" }, `workflow ${activeWorkflow.workflow_id}`),
              h("p", { className: "mono" }, `request ${activeWorkflow.request_id}`),
            )
          : h("p", { className: "muted" }, "None"),
        id,
      );
    }

    if (id === "log") {
      return panel("Log", h("pre", { className: "live-log" }, logs.join("\n")), id);
    }

    if (id === "raw") {
      return panel("Raw", h("pre", { className: "raw-pre" }, JSON.stringify(artifactData || workflow || {}, null, 2)), id);
    }

    return null;
  };

  return h(
    "main",
    { className: "shell" },
    h("div", { className: "backdrop glow-a" }),
    h("div", { className: "backdrop glow-b" }),
    h("div", { className: "backdrop mesh" }),
    h(
      "header",
      { className: "top panel" },
      h("div", null, h("h1", null, "Agent Pods Ops Console"), h("p", null, "One workflow at a time. Prompt-driven layout and execution.")),
      h("div", { className: "chips-row" }, chip("react"), chip("single workflow"), chip("layout commands")),
    ),
    h(
      "section",
      { className: "panel composer" },
      h("label", { htmlFor: "prompt" }, "Prompt"),
      h("textarea", {
        id: "prompt",
        value: prompt,
        onInput: (e) => setPrompt(e.target.value),
        placeholder: "build a simple CRUD application | move timeline to sidebar",
      }),
      h(
        "div",
        { className: "controls" },
        h(
          "label",
          { className: "mode-select" },
          "Mode",
          h(
            "select",
            { value: mode, onChange: (e) => setMode(e.target.value) },
            h("option", { value: "workflow" }, "Workflow"),
            h("option", { value: "hello_service" }, "Hello Service"),
            h("option", { value: "service_bootstrap" }, "Service Bootstrap"),
            h("option", { value: "research" }, "Research"),
          ),
        ),
        h("button", { onClick: runPrompt, disabled: running || Boolean(activeWorkflow) }, running ? "Running..." : "Run"),
        h("input", {
          type: "text",
          value: artifactId,
          onInput: (e) => setArtifactId(e.target.value),
          placeholder: "artifact id (art_...)",
        }),
        h("button", { onClick: loadArtifact }, "Load Artifact"),
      ),
    ),
    h("section", { className: "layout-grid" }, h("div", { className: "layout-main" }, ...layout.main.map(widget).filter(Boolean)), h("aside", { className: "layout-sidebar" }, ...layout.sidebar.map(widget).filter(Boolean))),
    h("section", { className: "layout-bottom" }, ...layout.bottom.map(widget).filter(Boolean)),
  );
}

createRoot(document.getElementById("root")).render(h(App));
