"use client";

import { type FormEvent, useState } from "react";
import {
  api, displayName, formattedTime, type CaseResult, type Fixture, type Run, type RunDetail, type Suite,
} from "./api-client";
import HttpEvaluations from "./http-evaluations";

type Session = { token: string; suites: Suite[]; runs: Run[] };
const MAX_SUITE_BYTES = 128 * 1024;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "The request failed.";
}

function Outcome({ passed }: { passed: boolean }) {
  return <span className={passed ? "outcome pass" : "outcome fail"}>{passed ? "Pass" : "Fail"}</span>;
}

function CaseDetails({ result }: { result: CaseResult }) {
  return (
    <details className="case-card">
      <summary>
        <Outcome passed={result.passed} />
        <span className="case-id">{result.case_id}</span>
        <span className="case-outcome">{displayName(result.outcome)}</span>
        {result.critical && <span className="critical">Critical</span>}
      </summary>
      <div className="case-body">
        <h4>Assertions</h4>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Check</th><th>Expected</th><th>Actual</th><th>Result</th></tr></thead>
            <tbody>
              {result.assertions.map((assertion, index) => (
                <tr key={`${assertion.check}-${index}`}>
                  <td>{displayName(assertion.check)}</td>
                  <td><code>{JSON.stringify(assertion.expected)}</code></td>
                  <td><code>{JSON.stringify(assertion.actual)}</code></td>
                  <td><Outcome passed={assertion.passed} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h4>Trace events</h4>
        <ol className="trace">
          {result.trace.map((event) => (
            <li key={event.seq}>
              <code className="event-number">{event.seq}</code>
              <span>
                {displayName(event.kind)}
                {event.tool && <code className="trace-meta">{event.tool}</code>}
                {event.attempt !== null && <small className="trace-meta">Attempt {event.attempt}</small>}
              </span>
              <code>{event.elapsed_ms} ms</code>
            </li>
          ))}
        </ol>
      </div>
    </details>
  );
}

function RunComparison({ current, baseline }: { current: RunDetail; baseline: RunDetail }) {
  const baselineCases = new Map(baseline.results.map((result) => [result.case_id, result]));
  const currentCases = new Map(current.results.map((result) => [result.case_id, result]));
  const ids = new Set([...currentCases.keys(), ...baselineCases.keys()]);

  return (
    <div className="table-scroll comparison" aria-label="Run comparison results">
      <table>
        <caption>Case outcomes for both stored runs</caption>
        <thead><tr><th>Case</th><th>Selected · {current.fixture}</th><th>Baseline · {baseline.fixture}</th></tr></thead>
        <tbody>
          {[...ids].map((caseId) => {
            const selectedCase = currentCases.get(caseId);
            const baselineCase = baselineCases.get(caseId);
            return (
              <tr key={caseId}>
                <td><code>{caseId}</code></td>
                <td>{selectedCase ? <Outcome passed={selectedCase.passed} /> : "Absent"}</td>
                <td>{baselineCase ? <Outcome passed={baselineCase.passed} /> : "Absent"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Workspace({ session }: { session: Session }) {
  const { token } = session;
  const [suites, setSuites] = useState(session.suites);
  const [runs, setRuns] = useState(session.runs);
  const [suiteId, setSuiteId] = useState(session.suites[0]?.id ?? "");
  const [fixture, setFixture] = useState<Fixture>("corrected");
  const [suiteInput, setSuiteInput] = useState("");
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [baseline, setBaseline] = useState<RunDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      await action();
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function createSuite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void perform(async () => {
      if (new TextEncoder().encode(suiteInput).length > MAX_SUITE_BYTES) {
        throw new Error("Suite JSON exceeds 128 KiB.");
      }
      try {
        JSON.parse(suiteInput);
      } catch {
        throw new Error("Enter valid JSON for the suite.");
      }
      const suite = await api<Suite>(token, "/suites", { method: "POST", body: suiteInput });
      setSuites((current) => [suite, ...current]);
      setSuiteId(suite.id);
      setSuiteInput("");
      setStatus(`Created suite ${suite.name}.`);
    });
  }

  async function readSuiteFile(file: File) {
    setError("");
    if (file.size > MAX_SUITE_BYTES) {
      setError("Suite JSON exceeds 128 KiB.");
      return;
    }
    try {
      setSuiteInput(await file.text());
    } catch {
      setError("Could not read the selected file.");
    }
  }

  function showRun(run: RunDetail) {
    setSelectedRun(run);
    setBaseline(null);
  }

  function acceptRun(run: RunDetail) {
    setRuns((current) => [run, ...current.filter(({ id }) => id !== run.id)]);
    showRun(run);
  }

  function startRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!suiteId) return;
    void perform(async () => {
      const run = await api<RunDetail>(token, `/suites/${suiteId}/runs`, {
        method: "POST", body: JSON.stringify({ fixture }),
      });
      acceptRun(run);
      setStatus(`Evaluation completed with ${run.failed_count} failed cases.`);
    });
  }

  function selectRun(id: string) {
    void perform(async () => showRun(await api<RunDetail>(token, `/runs/${id}`)));
  }

  function compareRun(id: string) {
    if (!id) {
      setBaseline(null);
      return;
    }
    void perform(async () => setBaseline(await api<RunDetail>(token, `/runs/${id}`)));
  }

  function replayRun(id: string) {
    void perform(async () => {
      const run = await api<RunDetail>(token, `/runs/${id}/replay`, { method: "POST" });
      acceptRun(run);
      setStatus("Replay completed and saved.");
    });
  }

  return (
    <>
      <div className="notices">
        {error && <p className="notice error" role="alert">{error}</p>}
        <div role="status" aria-live="polite">{status && <p className="notice success">{status}</p>}</div>
      </div>
      <div className="workspace">
        <div className="controls">
          <section className="panel" aria-labelledby="new-run-title">
            <h2 id="new-run-title">New evaluation</h2>
            <form onSubmit={startRun}>
              <label htmlFor="suite">Test suite</label>
              <select id="suite" value={suiteId} onChange={(event) => setSuiteId(event.target.value)} disabled={busy || suites.length === 0}>
                {suites.length === 0 && <option value="">Create a suite first</option>}
                {suites.map((suite) => <option key={suite.id} value={suite.id}>{suite.name}</option>)}
              </select>
              <label htmlFor="fixture">Fixture agent</label>
              <select id="fixture" value={fixture} onChange={(event) => setFixture(event.target.value as Fixture)} disabled={busy}>
                <option value="corrected">Corrected</option>
                <option value="vulnerable">Vulnerable</option>
              </select>
              <button className="primary" disabled={busy || !suiteId}>Run evaluation</button>
            </form>
          </section>

          <section className="panel" aria-labelledby="suite-title">
            <h2 id="suite-title">Create suite</h2>
            <form onSubmit={createSuite}>
              <label className="file-input">
                Upload suite JSON
                <input type="file" accept=".json,application/json" disabled={busy} onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) void readSuiteFile(file);
                  event.currentTarget.value = "";
                }} />
              </label>
              <label htmlFor="suite-json">Suite JSON</label>
              <textarea id="suite-json" rows={5} spellCheck={false} value={suiteInput} onChange={(event) => setSuiteInput(event.target.value)} required disabled={busy} placeholder={'{ "version": "1", "name": "…", "cases": […] }'} />
              <button disabled={busy || !suiteInput.trim()}>Save suite</button>
            </form>
          </section>
        </div>

        <section className="panel ledger" aria-labelledby="runs-title">
          <div className="panel-header">
            <h2 id="runs-title">Run history</h2>
            <span className="secondary">{runs.length} loaded</span>
          </div>
          {runs.length === 0 ? (
            <p className="empty">Run a suite to see saved outcomes here.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead><tr><th>Suite</th><th>Fixture</th><th>Failed cases</th><th>Result</th></tr></thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id} className={selectedRun?.id === run.id ? "selected" : ""}>
                      <td>
                        <button type="button" className="run-link" disabled={busy} aria-pressed={selectedRun?.id === run.id} onClick={() => selectRun(run.id)}>
                          <strong>{run.suite_name}</strong>
                          <small>{formattedTime(run.created_at)} · {run.id.slice(0, 8)}</small>
                        </button>
                      </td>
                      <td className="fixture">{run.fixture}{run.replay_of && <small className="replayed">Replay</small>}</td>
                      <td><code>{run.failed_count} / {run.case_count}</code></td>
                      <td><Outcome passed={run.passed} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {selectedRun && (
            <section className="inspection" aria-labelledby="details-title">
              <div className="detail-header">
                <div>
                  <h3 id="details-title">Run details</h3>
                  <p><strong>{selectedRun.suite_name}</strong> · <span className="fixture">{selectedRun.fixture}</span></p>
                </div>
                <button type="button" disabled={busy} onClick={() => replayRun(selectedRun.id)}>Replay run</button>
              </div>
              <dl className="run-meta">
                <div><dt>Evidence</dt><dd>{displayName(selectedRun.evidence)}</dd></div>
                <div><dt>Critical failures</dt><dd>{selectedRun.critical_failed}</dd></div>
                <div><dt>Engine</dt><dd>{selectedRun.engine_version}</dd></div>
                <div><dt>Specification</dt><dd>{selectedRun.spec_version}</dd></div>
                <div><dt>Run ID</dt><dd><code>{selectedRun.id}</code></dd></div>
                {selectedRun.replay_of && <div><dt>Replayed from</dt><dd><code>{selectedRun.replay_of}</code></dd></div>}
              </dl>
              <div className="case-list">
                {selectedRun.results.map((result) => <CaseDetails key={`${selectedRun.id}/${result.case_id}`} result={result} />)}
              </div>
              <div className="comparison-controls">
                <label htmlFor="baseline">Compare with</label>
                <select id="baseline" value={baseline?.id ?? ""} onChange={(event) => compareRun(event.target.value)} disabled={busy}>
                  <option value="">Select another run of this suite</option>
                  {runs.filter((run) => run.suite_id === selectedRun.suite_id && run.id !== selectedRun.id).map((run) => (
                    <option key={run.id} value={run.id}>
                      {run.fixture} · {formattedTime(run.created_at)} · {run.id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              </div>
              {baseline && <RunComparison current={selectedRun} baseline={baseline} />}
            </section>
          )}
        </section>
      </div>
    </>
  );
}

function ConnectedWorkspace({ session }: { session: Session }) {
  const [view, setView] = useState<"fixture" | "http">("fixture");
  const [visitedHttp, setVisitedHttp] = useState(false);
  return (
    <>
      <nav className="workflow-nav" aria-label="Evaluation workflows">
        <button type="button" aria-pressed={view === "fixture"} onClick={() => setView("fixture")}>Fixture evaluations</button>
        <button type="button" aria-pressed={view === "http"} onClick={() => {
          setVisitedHttp(true);
          setView("http");
        }}>HTTP evaluations</button>
      </nav>
      <div hidden={view !== "fixture"}><Workspace session={session} /></div>
      {visitedHttp && <div hidden={view !== "http"}><HttpEvaluations token={session.token} /></div>}
    </>
  );
}

export default function Home() {
  const [token, setToken] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");

  async function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setConnecting(true);
    setError("");
    try {
      const credential = token.trim();
      const [{ suites }, { runs }] = await Promise.all([
        api<{ suites: Suite[] }>(credential, "/suites"),
        api<{ runs: Run[] }>(credential, "/runs"),
      ]);
      setSession({ token: credential, suites, runs });
      setToken("");
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setConnecting(false);
    }
  }

  return (
    <>
      <header className="site-header">
        <div className="shell site-row">
          <span className="wordmark"><span className="mark" aria-hidden="true" />AgentProof</span>
          {session && <button className="disconnect" type="button" onClick={() => setSession(null)}>Disconnect</button>}
        </div>
      </header>
      <main className="shell">
        <div className="page-heading">
          <h1>Evaluations</h1>
          <p>Run fixture evaluations and HTTP benchmarks, then inspect the evidence behind each result.</p>
        </div>
        {session ? (
          <ConnectedWorkspace session={session} />
        ) : (
          <section className="panel connect-panel" aria-labelledby="connect-title">
            <h2 id="connect-title">Connect to AgentProof</h2>
            <form onSubmit={connect}>
              <label htmlFor="api-token">API token</label>
              <input id="api-token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} required />
              <button className="primary" disabled={connecting || !token.trim()}>{connecting ? "Connecting…" : "Connect"}</button>
              {error && <p className="notice error" role="alert">{error}</p>}
            </form>
          </section>
        )}
      </main>
    </>
  );
}
