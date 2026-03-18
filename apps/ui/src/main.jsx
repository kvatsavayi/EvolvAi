import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../styles.css';

function Chip({ text, tone = '' }) {
  return <span className={`chip ${tone}`.trim()}>{text}</span>;
}

function Panel({ title, children }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function App() {
  const [prompt, setPrompt] = useState('');
  const [mode, setMode] = useState('workflow');
  const [running, setRunning] = useState(false);
  const [workflow, setWorkflow] = useState(null);
  const [pendingClarification, setPendingClarification] = useState(null);
  const [clarificationAnswers, setClarificationAnswers] = useState({});
  const [artifactId, setArtifactId] = useState('');
  const [artifactData, setArtifactData] = useState(null);
  const [activeWorkflow, setActiveWorkflow] = useState(null);
  const [health, setHealth] = useState({ status: 'unknown' });
  const [requestDetails, setRequestDetails] = useState(null);
  const [attractorSummary, setAttractorSummary] = useState([]);
  const [logs, setLogs] = useState([]);
  const runControllerRef = useRef(null);

  const appendLog = (line) => {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev.slice(-400), `[${ts}] ${line}`]);
  };

  const summary = useMemo(() => {
    if (!workflow) return null;
    const steps = Array.isArray(workflow.steps) ? workflow.steps : [];
    return {
      target: workflow.canonical_target || 'n/a',
      workflowId: workflow.workflow_id || 'n/a',
      requestId: workflow.request_id || 'n/a',
      finalStatus: workflow.final_status || 'n/a',
      finalPass: Boolean(workflow.final_pass),
      steps,
      passCount: steps.filter((s) => s.pass).length,
      serviceUrl: workflow.service_hello_url || workflow.service_url || '',
    };
  }, [workflow]);

  const diagnostics = useMemo(() => {
    const runDetails = Array.isArray(requestDetails?.run_details) ? requestDetails.run_details : [];
    let totalTokens = 0;
    let completionTokens = 0;
    let promptTokens = 0;
    let workspaceOps = 0;
    let bytesWritten = 0;
    let knowledgeReads = 0;
    let knowledgeCommits = 0;
    let attemptCount = 0;
    for (const run of runDetails) {
      const attempts = Array.isArray(run.attempts) ? run.attempts : [];
      for (const a of attempts) {
        const tc = a.token_counts || {};
        totalTokens += Number(tc.total_tokens || 0);
        completionTokens += Number(tc.completion_tokens || 0);
        promptTokens += Number(tc.prompt_tokens || 0);
        workspaceOps += Number(a.workspace_ops_count || 0);
        bytesWritten += Number(a.bytes_written || 0);
        knowledgeReads += Number(a.knowledge_reads_count || 0);
        knowledgeCommits += Number(a.knowledge_commits_count || 0);
        attemptCount += 1;
      }
    }
    return {
      runCount: runDetails.length,
      attemptCount,
      totalTokens,
      completionTokens,
      promptTokens,
      workspaceOps,
      bytesWritten,
      knowledgeReads,
      knowledgeCommits,
    };
  }, [requestDetails]);

  async function refreshHealthAndActivity() {
    try {
      const [hRes, aRes, atRes] = await Promise.all([
        fetch('/health'),
        fetch('/v1/workflows/active'),
        fetch('/v1/attractors?window=5&limit=3'),
      ]);
      const h = await hRes.json();
      const a = await aRes.json();
      const at = await atRes.json();
      setHealth(h || { status: 'unknown' });
      setActiveWorkflow(a?.active ? a.workflow : null);
      setAttractorSummary(Array.isArray(at?.items) ? at.items : []);
    } catch {
      // keep last values
    }
  }

  async function loadRequestDetails(requestId) {
    if (!requestId) return;
    try {
      const res = await fetch(`/v1/requests/${encodeURIComponent(requestId)}?include_run_details=true&include_attempts=true`);
      if (!res.ok) return;
      const payload = await res.json();
      setRequestDetails(payload);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    refreshHealthAndActivity();
    const timer = setInterval(refreshHealthAndActivity, 3000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const requestId = workflow?.request_id || pendingClarification?.request_id;
    if (requestId) loadRequestDetails(requestId);
  }, [workflow, pendingClarification]);

  async function runPrompt() {
    const text = String(prompt || '').trim();
    if (!text) return;
    if (running) {
      appendLog('request ignored: workflow already running in UI');
      return;
    }

    setRunning(true);
    setWorkflow(null);
    setPendingClarification(null);
    setClarificationAnswers({});
    setRequestDetails(null);
    appendLog(`mode=${mode} started`);

    const controller = new AbortController();
    runControllerRef.current = controller;

    try {
      if (mode === 'research') {
        const res = await fetch('/v1/research', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: text, max_hits: 20 }),
          signal: controller.signal,
        });
        const payload = await res.json();
        setWorkflow({
          workflow_id: payload.research_report_id,
          request_id: payload.run_id || 'research',
          canonical_target: 'research',
          final_status: 'done',
          final_pass: true,
          steps: [],
          raw: payload,
        });
        appendLog(`research report=${payload.research_report_id}`);
        return;
      }

      const body = {
        user_input: text,
        request_type: mode === 'hello_service' || mode === 'service_bootstrap' ? 'coding' : 'auto',
        max_steps: mode === 'hello_service' || mode === 'service_bootstrap' ? 8 : 6,
        retry_same_persona_once: true,
        learn_mode: true,
      };
      if (mode === 'hello_service') body.canonical_target = 'hello_fastapi_service';
      if (mode === 'service_bootstrap') body.canonical_target = 'service_bootstrap_app';

      const res = await fetch('/v1/workflows', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const payload = await res.json();
      if (!res.ok) {
        appendLog(`workflow error ${res.status}: ${JSON.stringify(payload)}`);
        return;
      }
      setWorkflow(payload);
      if (payload.workflow_graph_artifact_id) setArtifactId(payload.workflow_graph_artifact_id);
      if (payload.final_status === 'needs_clarification' && payload.clarification) {
        setPendingClarification(payload.clarification);
        const initial = {};
        for (const q of payload.clarification.questions || []) initial[q.id] = '';
        setClarificationAnswers(initial);
        appendLog(`clarification needed request=${payload.request_id}`);
      } else {
        appendLog(`workflow ${payload.workflow_id} final=${payload.final_status} pass=${Boolean(payload.final_pass)}`);
        if (payload.service_hello_url) appendLog(`service ${payload.service_hello_url}`);
      }
    } catch (err) {
      if (err?.name === 'AbortError') {
        appendLog('stopped waiting for workflow response');
      } else {
        appendLog(`request exception: ${String(err)}`);
      }
    } finally {
      runControllerRef.current = null;
      setRunning(false);
      refreshHealthAndActivity();
    }
  }

  async function submitClarifications() {
    if (!pendingClarification?.request_id || running) return;
    const answers = Object.fromEntries(
      Object.entries(clarificationAnswers || {}).filter(([, v]) => String(v || '').trim())
    );
    if (Object.keys(answers).length === 0) {
      appendLog('clarification answers required');
      return;
    }
    setRunning(true);
    try {
      const res = await fetch(`/v1/workflows/${encodeURIComponent(pendingClarification.request_id)}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ answers }),
      });
      const payload = await res.json();
      if (!res.ok) {
        appendLog(`resume error ${res.status}: ${JSON.stringify(payload)}`);
        return;
      }
      setPendingClarification(null);
      setClarificationAnswers({});
      setWorkflow(payload);
      if (payload.workflow_graph_artifact_id) setArtifactId(payload.workflow_graph_artifact_id);
      appendLog(`resumed workflow ${payload.workflow_id} final=${payload.final_status}`);
      if (payload.service_hello_url) appendLog(`service ${payload.service_hello_url}`);
    } catch (err) {
      appendLog(`resume exception: ${String(err)}`);
    } finally {
      setRunning(false);
      refreshHealthAndActivity();
    }
  }

  function stopWorkflow() {
    if (runControllerRef.current) {
      runControllerRef.current.abort();
      runControllerRef.current = null;
    }
    setRunning(false);
    appendLog('stop requested (client wait aborted)');
  }

  function resetWorkflowView() {
    setWorkflow(null);
    setPendingClarification(null);
    setClarificationAnswers({});
    setArtifactData(null);
    setRequestDetails(null);
    appendLog('ui state reset');
  }

  async function loadArtifact() {
    const id = String(artifactId || '').trim();
    if (!id) return;
    try {
      const res = await fetch(`/v1/artifacts/${encodeURIComponent(id)}`);
      const payload = await res.json();
      if (!res.ok) throw new Error(JSON.stringify(payload));
      setArtifactData(payload);
      appendLog(`artifact loaded ${id}`);
    } catch (err) {
      appendLog(`artifact load error: ${String(err)}`);
    }
  }

  return (
    <main className="shell tri-shell">
      <header className="panel top">
        <div>
          <h1>Agent Pods Control Surface</h1>
          <p>Model expression, operator controls, and diagnostics in one view.</p>
        </div>
        <div className="chips-row">
          <Chip text={`health ${health.status || 'unknown'}`} tone={health.status === 'ok' ? 'ok' : ''} />
          <Chip text={running ? 'running' : 'idle'} tone={running ? 'warn' : 'ok'} />
          <Chip text={activeWorkflow ? 'workflow active' : 'no active workflow'} />
        </div>
      </header>

      <section className="tri-top">
        <Panel title="Model Expression">
          <div className="chips-row">
            <Chip text={`target ${summary?.target || 'n/a'}`} />
            <Chip text={`status ${summary?.finalStatus || 'n/a'}`} tone={summary?.finalPass ? 'ok' : summary ? 'fail' : ''} />
            <Chip text={`steps ${summary?.steps?.length || 0}`} />
          </div>
          <p className="assistant-text">
            {summary
              ? `Workflow ${summary.workflowId} completed ${summary.passCount}/${summary.steps.length} passing steps.`
              : 'No workflow result yet.'}
          </p>
          {summary?.serviceUrl ? <p className="mono">{summary.serviceUrl}</p> : null}
          <div className="mini-grid">
            <div>
              <strong>Planner</strong>
              <ul>
                {((workflow?.planner?.plan_steps || []).slice(0, 5)).map((x, i) => (
                  <li key={i}>{String(x)}</li>
                ))}
              </ul>
            </div>
            <div>
              <strong>Timeline</strong>
              <ul>
                {((summary?.steps || []).slice(0, 8)).map((s, i) => (
                  <li key={`${s.run_id || i}`}>{`#${i + 1} ${s.persona_id} ${s.pass ? 'pass' : 'fail'}`}</li>
                ))}
              </ul>
            </div>
          </div>
        </Panel>

        <Panel title="Operator Interface">
          <label htmlFor="prompt">Prompt</label>
          <textarea
            id="prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Describe the change..."
          />
          <div className="controls">
            <label className="mode-select">
              Mode
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="workflow">Workflow</option>
                <option value="hello_service">Hello Service</option>
                <option value="service_bootstrap">Service Bootstrap</option>
                <option value="research">Research</option>
              </select>
            </label>
            <button onClick={runPrompt} disabled={running}>Run</button>
            <button onClick={stopWorkflow}>Stop</button>
            <button onClick={resetWorkflowView}>Reset</button>
          </div>

          {pendingClarification ? (
            <div className="clarification-box">
              <p className="mono">{`request ${pendingClarification.request_id}`}</p>
              {(pendingClarification.questions || []).map((q) => (
                <label key={q.id} className="clarification-item">
                  <span>{q.question}</span>
                  <input
                    type="text"
                    value={clarificationAnswers[q.id] || ''}
                    onChange={(e) =>
                      setClarificationAnswers((prev) => ({
                        ...prev,
                        [q.id]: e.target.value,
                      }))
                    }
                    placeholder="answer"
                  />
                </label>
              ))}
              <button onClick={submitClarifications} disabled={running}>Submit Clarifications</button>
            </div>
          ) : null}

          <div className="artifact-tools">
            <input
              type="text"
              value={artifactId}
              onChange={(e) => setArtifactId(e.target.value)}
              placeholder="artifact id (art_...)"
            />
            <button onClick={loadArtifact}>Load Artifact</button>
          </div>
        </Panel>
      </section>

      <section className="tri-bottom panel">
        <h2>Diagnostics And Health</h2>
        <div className="diag-grid">
          <div>
            <div className="chips-row">
              <Chip text={`request ${summary?.requestId || pendingClarification?.request_id || 'n/a'}`} />
              <Chip text={`active ${activeWorkflow?.workflow_id || 'none'}`} />
            </div>
            <ul className="diag-list">
              <li>{`runs: ${diagnostics.runCount}`}</li>
              <li>{`attempts: ${diagnostics.attemptCount}`}</li>
              <li>{`tokens total/prompt/completion: ${diagnostics.totalTokens}/${diagnostics.promptTokens}/${diagnostics.completionTokens}`}</li>
              <li>{`workspace ops: ${diagnostics.workspaceOps}`}</li>
              <li>{`bytes written: ${diagnostics.bytesWritten}`}</li>
              <li>{`knowledge reads/commits: ${diagnostics.knowledgeReads}/${diagnostics.knowledgeCommits}`}</li>
            </ul>
            <strong>Attractor Snapshot</strong>
            <ul className="diag-list">
              {attractorSummary.map((x, i) => (
                <li key={i}>{`${x.behavior_fp || 'n/a'} count=${x.count_total || 0} repair=${x.repair_rate || 0}`}</li>
              ))}
            </ul>
          </div>
          <div>
            <strong>Rolling Log</strong>
            <pre className="live-log">{logs.join('\n')}</pre>
            <strong>Raw Payload</strong>
            <pre className="raw-pre">{JSON.stringify(artifactData || workflow || requestDetails || {}, null, 2)}</pre>
          </div>
        </div>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')).render(<App />);
