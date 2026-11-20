# Architecture

AgentProof evaluates trusted, scripted fixture implementations using a versioned suite and an isolated fake provider. FastAPI serves the dashboard and scopes stored suites and runs to authenticated workspaces. The release-gate CLI loads a suite from disk and uses the same Python evaluator without a database.

```mermaid
flowchart LR
    Browser["Next.js dashboard"] --> API["FastAPI API"]
    API --> Engine["Deterministic Python evaluator"]
    CLI["Release gate CLI"] --> Engine
    Suite["Versioned JSON or YAML suite"] --> CLI
    API <--> DB[("PostgreSQL")]
    Engine --> Fixture["Scripted fixture agent"]
    Engine --> Simulation["Fake tools and provider"]
```

## Scope and assumptions

The initial repository at `8c4758d` contained a README and Apache 2.0 license. The MVP assumes a developer-controlled local instance of PostgreSQL, an isolated Compose stack, and bounded fixture input. Its workspace tokens support local development and API authorization tests. Use the bundled fixture suite to reproduce the examples.

The application path is synchronous and bounded. The simulated worker interruption is an outcome in the evaluator. There is no background job broker, production worker recovery protocol, arbitrary agent-code execution, or required telemetry collector. Structured trace events use stable sequence numbers and simulated elapsed time. OpenTelemetry transport can be assessed when there is an observed collector or cross-service instrumentation requirement.

Docker and Compose pin Python 3.12, Node.js 24, and PostgreSQL 17 images by digest. The Python and frontend lockfiles pin direct and transitive dependencies. PostgreSQL supplies JSONB for versioned records, foreign keys for run provenance, transactions for atomic results, and advisory locks for migrations and evaluation concurrency.

## Evaluation and release gating

A suite declares its version, name, and cases. Each case specifies input, expected response and outcome, a simulated tool allowlist, policy assertions, scripted provider behavior, and virtual time and retry limits. Result assertions expose the check name and expected and actual values when safe. The report records pass/fail, outcome, and ordered trace events per case.

The evaluator uses the suite and fixture selection as input. It emits a report with version identifiers and no wall-clock timestamps or random IDs. Policy checks evaluate attempts as well as tool effects, allowing a failed security assertion even when the provider-side protection prevented the effect.

The release gate exits with a failure status when a critical case fails. Inspect individual assertions to diagnose failure. Suite-wide pass/fail also reflects failures on noncritical cases.

## Storage and replay

SQL migrations live in `backend/agentproof/migrations`. PostgreSQL stores immutable suites, execution metadata, and per-case results including sanitized traces. The migration runner records each migration and its checksum. It applies pending migrations under a transactional advisory lock. Workspace-scoped queries govern suite, run, and replay access. Composite foreign keys keep a run and its replay source in the same suite and workspace.

Original run and replay records have independent IDs and timestamps. Replaying a run loads its stored suite and fixture and checks that the reconstructed deterministic result matches the stored report. The replay links to its source. A transaction writes the run and all of its case results together. The API refuses replay if the original engine version differs or if the reconstructed assertions or traces diverge.

| Record | Contents |
| --- | --- |
| Suite | Workspace ownership, identifier, name, versioned specification, creation time |
| Run | Suite reference, fixture and version metadata, verdict, replay source, creation time |
| Result | Case ID, critical flag, outcome, assertion results, ordered trace |
| Migration | Applied migration identifier, contents checksum, apply time |

## Milestones and checks

| Slice | Acceptance check |
| --- | --- |
| Evaluation and release gate | Same inputs produce the same report. Corrected fixture passes and vulnerable fixture fails the same suite. |
| Storage and isolation | Test suite and run creation against migrated PostgreSQL. Replay preserves the report. Two workspaces cannot read or replay each other's records. |
| Dashboard | Create a suite, run both fixtures, inspect a stored failure and trace, compare verdicts, and replay. |
| Defensive boundaries | Simulate blocked and duplicate actions, timeouts, malformed responses, exhausted retries, and interruption. Test HTTP adapter limits through a fake transport. |
| Reproducibility | Run builds, tests, dependency audits, and gate checks on the proposed revision. Record each result and any unavailable check. |
