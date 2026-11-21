"use client";

import { type FormEvent, useEffect, useState } from "react";
import { api, displayName, formattedTime } from "./api-client";

type EvidenceMode = "local_reference_http" | "external_model_http";

type Agent = {
  id: string;
  name: string;
  description: string;
  evidence_mode: EvidenceMode;
  model: string;
  agent_version: string | null;
};

type BenchmarkSuite = {
  id: string;
  name: string;
  description: string;
  version: string;
};

type HttpRun = {
  id: string;
  agent_id: string;
  agent_name: string;
  agent_version: string | null;
  suite_id: string;
  suite_name: string;
  suite_version: string;
  dataset_digest: string;
  evidence: EvidenceMode;
  model: string;
  created_at: string;
  engine_version: string;
  adapter_version: string;
  correlation_id: string;
  passed: boolean;
  case_count: number;
  failed_count: number;
  critical_failed: number;
  cost_usd: number | null;
};

type TraceEvent = {
  seq: number;
  kind: string;
  elapsed_ms: number;
  tool: string | null;
  attempt: number | null;
  normalized_arguments: { ticket_id: string } | null;
};

type CaseResult = {
  case_id: string;
  critical: boolean;
  passed: boolean;
  task_passed: boolean | null;
  policy_passed: boolean;
  expected_label: string | null;
  observed_label: string | null;
  latency_ms: number;
  expected_outcome: string;
  outcome: string;
  outcome_passed: boolean;
  fault_injection: string;
  adapter_calls: number;
  required_tool_reads: number;
  tool_reads: number;
  trace: TraceEvent[];
};

type HttpRunDetail = HttpRun & {
  results: CaseResult[];
  metrics: {
    policy_passed_count: number;
    task_case_count: number;
    task_passed_count: number;
    latency_ms: number;
    adapter_calls: number;
    usage_reported_calls: number;
    token_usage: { input_tokens: number; output_tokens: number } | null;
  };
};

const version = "v2" as const;

function evidenceLabel(mode: EvidenceMode) {
  return mode === "local_reference_http" ? "Local HTTP reference" : "External model HTTP";
}

function Verdict({ passed }: { passed: boolean }) {
  return <span className={`outcome ${passed ? "pass" : "fail"}`}>{passed ? "Pass" : "Fail"}</span>;
}

function CaseDetails({ result }: { result: CaseResult }) {
  return (
    <details className="case-card">
      <summary>
        <Verdict passed={result.passed} />
        <span className="case-id">{result.case_id}</span>
        <span className="case-outcome">{displayName(result.outcome)}</span>
        {!result.policy_passed && <span className="outcome fail">Policy failure</span>}
        {result.critical && <span className="critical">Critical</span>}
      </summary>
      <div className="case-body">
        <dl className="run-meta">
          <div><dt>Task</dt><dd>{result.task_passed === null ? "Not scored" : <Verdict passed={result.task_passed} />}</dd></div>
          <div><dt>Policy</dt><dd><Verdict passed={result.policy_passed} /></dd></div>
          <div><dt>Expected outcome</dt><dd>{displayName(result.expected_outcome)} · <Verdict passed={result.outcome_passed} /></dd></div>
          {result.expected_label && <div><dt>Expected classification</dt><dd>{result.expected_label}</dd></div>}
          {result.expected_label && <div><dt>Observed classification</dt><dd>{result.observed_label ?? "Unavailable"}</dd></div>}
          <div><dt>Elapsed</dt><dd>{result.latency_ms} ms</dd></div>
          <div><dt>Fault injection</dt><dd>{displayName(result.fault_injection)}</dd></div>
          <div><dt>Adapter calls</dt><dd>{result.adapter_calls}</dd></div>
          <div><dt>Tool reads</dt><dd>{result.tool_reads} / {result.required_tool_reads} required</dd></div>
        </dl>
        <h4>Trace events</h4>
        <ol className="trace">
          {result.trace.map((event) => (
            <li key={event.seq}>
              <code className="event-number">{event.seq}</code>
              <span>
                {displayName(event.kind)}
                {event.tool && <code className="trace-meta">{event.tool}</code>}
                {event.normalized_arguments && <code className="trace-meta">{event.normalized_arguments.ticket_id}</code>}
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

function Comparison({ selected, baseline }: { selected: HttpRunDetail; baseline: HttpRunDetail }) {
  const baselineResults = new Map(baseline.results.map((result) => [result.case_id, result]));

  return (
    <div className="table-scroll comparison">
      <table>
        <caption>Case comparison for the same benchmark dataset</caption>
        <thead>
          <tr>
            <th scope="col">Case</th>
            <th scope="col">Selected · {selected.agent_name}<small className="replayed">{selected.model} · {selected.agent_version ?? "Version unavailable"}</small><small className="replayed">{evidenceLabel(selected.evidence)}</small></th>
            <th scope="col">Baseline · {baseline.agent_name}<small className="replayed">{baseline.model} · {baseline.agent_version ?? "Version unavailable"}</small><small className="replayed">{evidenceLabel(baseline.evidence)}</small></th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Task cases passed</th>
            <td>{selected.metrics.task_passed_count} / {selected.metrics.task_case_count}</td>
            <td>{baseline.metrics.task_passed_count} / {baseline.metrics.task_case_count}</td>
          </tr>
          <tr>
            <th scope="row">Policy cases passed</th>
            <td>{selected.metrics.policy_passed_count} / {selected.case_count}</td>
            <td>{baseline.metrics.policy_passed_count} / {baseline.case_count}</td>
          </tr>
          <tr>
            <th scope="row">Elapsed</th>
            <td>{selected.metrics.latency_ms} ms</td>
            <td>{baseline.metrics.latency_ms} ms</td>
          </tr>
          {selected.results.map((result) => {
            const other = baselineResults.get(result.case_id);
            return (
              <tr key={result.case_id}>
                <td><code>{result.case_id}</code></td>
                <td><Verdict passed={result.passed} /> <small>{result.latency_ms} ms</small></td>
                <td>{other ? <><Verdict passed={other.passed} /> <small>{other.latency_ms} ms</small></> : "Unavailable"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function HttpEvaluations({ token }: { token: string }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [suites, setSuites] = useState<BenchmarkSuite[]>([]);
  const [runs, setRuns] = useState<HttpRun[]>([]);
  const [agentId, setAgentId] = useState("");
  const [suiteId, setSuiteId] = useState("");
  const [selected, setSelected] = useState<HttpRunDetail | null>(null);
  const [baseline, setBaseline] = useState<HttpRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const options = { version, signal: controller.signal };
    void Promise.all([
      api<{ agents: Agent[] }>(token, "/agents", options),
      api<{ suites: BenchmarkSuite[] }>(token, "/suites", options),
      api<{ runs: HttpRun[] }>(token, "/runs", options),
    ]).then(([agentList, suiteList, runList]) => {
      if (controller.signal.aborted) return;
      setAgents(agentList.agents);
      setSuites(suiteList.suites);
      setRuns(runList.runs);
      setAgentId((current) => agentList.agents.some((agent) => agent.id === current) ? current : agentList.agents[0]?.id ?? "");
      setSuiteId((current) => suiteList.suites.some((suite) => suite.id === current) ? current : suiteList.suites[0]?.id ?? "");
      setLoading(false);
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setError(error instanceof Error ? error.message : "Could not load HTTP evaluations.");
      setLoading(false);
    });
    return () => controller.abort();
  }, [token, refresh]);

  async function perform(progress: string, action: () => Promise<void>) {
    setBusy(progress);
    setError("");
    setStatus("");
    try {
      await action();
    } catch (error) {
      setError(error instanceof Error ? error.message : "The request failed.");
    } finally {
      setBusy("");
    }
  }

  function runBenchmark(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!agentId || !suiteId) return;
    void perform("Running benchmark…", async () => {
      const run = await api<HttpRunDetail>(token, "/runs", {
        version, method: "POST", body: JSON.stringify({ agent_id: agentId, suite_id: suiteId }),
      });
      setRuns((current) => [run, ...current.filter(({ id }) => id !== run.id)]);
      setSelected(run);
      setBaseline(null);
      setStatus(`Benchmark completed with ${run.failed_count} failed ${run.failed_count === 1 ? "case" : "cases"}.`);
    });
  }

  function selectRun(runId: string) {
    void perform("Loading run details…", async () => {
      const run = await api<HttpRunDetail>(token, `/runs/${runId}`, { version });
      setSelected(run);
      setBaseline(null);
    });
  }

  function compareRun(runId: string) {
    if (!runId) {
      setBaseline(null);
      return;
    }
    void perform("Loading baseline…", async () => {
      setBaseline(await api<HttpRunDetail>(token, `/runs/${runId}`, { version }));
    });
  }

  const activeAgent = agents.find(({ id }) => id === agentId);
  const activeSuite = suites.find(({ id }) => id === suiteId);
  const disabled = loading || Boolean(busy);
  const comparisonChoices = selected ? runs.filter((run) =>
    run.id !== selected.id && run.suite_id === selected.suite_id &&
    run.suite_version === selected.suite_version && run.dataset_digest === selected.dataset_digest &&
    run.engine_version === selected.engine_version,
  ) : [];

  return (
    <>
      <div className="notices">
        {error && <p className="notice error" role="alert">{error}</p>}
        <div role="status">{loading && <p className="notice">Loading HTTP evaluations…</p>}
          {busy && <p className="notice">{busy}</p>}
          {status && <p className="notice success">{status}</p>}
        </div>
      </div>
      <div className="workspace">
        <section className="panel" aria-labelledby="http-start-title">
          <h2 id="http-start-title">New HTTP benchmark</h2>
          <form onSubmit={runBenchmark}>
            <label htmlFor="http-agent">Agent configuration</label>
            <select id="http-agent" value={agentId} onChange={(event) => setAgentId(event.target.value)} disabled={disabled || agents.length === 0}>
              {agents.length === 0 && <option value="">No agents available</option>}
              {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </select>
            {activeAgent && <div className="control-help">
              <p>{activeAgent.description}</p>
              <small>{evidenceLabel(activeAgent.evidence_mode)} · {activeAgent.model} · {activeAgent.agent_version ?? "Version unavailable"}</small>
            </div>}
            <label htmlFor="http-suite">Benchmark suite</label>
            <select id="http-suite" value={suiteId} onChange={(event) => setSuiteId(event.target.value)} disabled={disabled || suites.length === 0}>
              {suites.length === 0 && <option value="">No suites available</option>}
              {suites.map((suite) => <option key={suite.id} value={suite.id}>{suite.name} · v{suite.version}</option>)}
            </select>
            {activeSuite && <p className="control-help">{activeSuite.description}</p>}
            <button className="primary" disabled={disabled || !agentId || !suiteId}>
              {activeAgent?.evidence_mode === "external_model_http" ? "Run external benchmark" : "Run benchmark"}
            </button>
          </form>
        </section>
        <section className="panel ledger" aria-labelledby="http-runs-title">
          <div className="panel-header">
            <h2 id="http-runs-title">HTTP run history</h2>
            <button disabled={disabled} type="button" onClick={() => {
              setLoading(true);
              setError("");
              setStatus("");
              setRefresh((value) => value + 1);
            }}>Refresh</button>
          </div>
          {runs.length === 0 ? (
            <p className="empty">Saved benchmark runs appear here.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead><tr><th>Suite</th><th>Agent</th><th>Failed cases</th><th>Result</th></tr></thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.id} className={selected?.id === run.id ? "selected" : ""}>
                      <td>
                        <button type="button" className="run-link" disabled={disabled} aria-pressed={selected?.id === run.id} onClick={() => selectRun(run.id)}>
                          <strong>{run.suite_name}</strong>
                          <small>{formattedTime(run.created_at)} · {run.id.slice(0, 8)}</small>
                        </button>
                      </td>
                      <td>{run.agent_name}<small className="replayed">{evidenceLabel(run.evidence)} · {run.agent_version ?? "Version unavailable"}</small></td>
                      <td><code>{run.failed_count} / {run.case_count}</code></td>
                      <td><Verdict passed={run.passed} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {selected && (
            <section className="inspection" aria-labelledby="http-details-title">
              <div className="detail-header">
                <div>
                  <h3 id="http-details-title">Benchmark run details</h3>
                  <p><strong>{selected.agent_name}</strong> · {selected.suite_name}</p>
                </div>
                <Verdict passed={selected.passed} />
              </div>
              <dl className="run-meta">
                <div><dt>Evidence</dt><dd>{evidenceLabel(selected.evidence)}</dd></div>
                <div><dt>Model identifier</dt><dd>{selected.model}</dd></div>
                <div><dt>Agent or model version</dt><dd>{selected.agent_version ?? "Unavailable"}</dd></div>
                <div><dt>Suite version</dt><dd>{selected.suite_version}</dd></div>
                <div><dt>Engine</dt><dd>{selected.engine_version}</dd></div>
                <div><dt>Adapter</dt><dd>{selected.adapter_version}</dd></div>
                <div><dt>Critical failures</dt><dd>{selected.critical_failed}</dd></div>
                <div><dt>Task cases passed</dt><dd>{selected.metrics.task_passed_count} / {selected.metrics.task_case_count}</dd></div>
                <div><dt>Policy cases passed</dt><dd>{selected.metrics.policy_passed_count} / {selected.case_count}</dd></div>
                <div><dt>Elapsed</dt><dd>{selected.metrics.latency_ms} ms</dd></div>
                <div><dt>Adapter calls</dt><dd>{selected.metrics.adapter_calls}</dd></div>
                <div><dt>Reported tokens</dt><dd>{selected.metrics.token_usage
                  ? `${selected.metrics.token_usage.input_tokens} input · ${selected.metrics.token_usage.output_tokens} output`
                  : "Unavailable"}</dd></div>
                <div><dt>Calls reporting usage</dt><dd>{selected.metrics.usage_reported_calls}</dd></div>
                <div><dt>Cost (USD)</dt><dd>{selected.cost_usd ?? "Unavailable"}</dd></div>
                <div><dt>Dataset digest</dt><dd><code>{selected.dataset_digest}</code></dd></div>
                <div><dt>{selected.id === selected.correlation_id ? "Run and correlation ID" : "Run ID"}</dt><dd><code>{selected.id}</code></dd></div>
                {selected.id !== selected.correlation_id && <div><dt>Correlation ID</dt><dd><code>{selected.correlation_id}</code></dd></div>}
              </dl>
              <div className="case-list">
                {selected.results.map((result) => <CaseDetails key={`${selected.id}/${result.case_id}`} result={result} />)}
              </div>
              <div className="comparison-controls">
                <label htmlFor="http-baseline">Compare benchmark with</label>
                <select id="http-baseline" value={baseline?.id ?? ""} onChange={(event) => compareRun(event.target.value)} disabled={disabled || comparisonChoices.length === 0}>
                  <option value="">{comparisonChoices.length === 0 ? "No other runs of this dataset" : "Select a baseline run"}</option>
                  {comparisonChoices.map((run) => (
                    <option key={run.id} value={run.id}>{run.agent_name} · {run.model} · {formattedTime(run.created_at)} · {run.id.slice(0, 8)}</option>
                  ))}
                </select>
              </div>
              {baseline && <Comparison selected={selected} baseline={baseline} />}
            </section>
          )}
        </section>
      </div>
    </>
  );
}
