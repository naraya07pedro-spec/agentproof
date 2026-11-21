# V2 pre-merge review

Reviewed PR #4 at `bc496a0ae31bf9352f1feae5551e566245e8c273` against `main` at `4e975ed6b124d11cc8bae303259733f369325dc7`. No demonstrable critical or high-severity defect was found within the documented local and opt-in scope. The review changes add regression coverage and clarify the operating boundaries.

## Findings

| Area | Observed controls and evidence |
| --- | --- |
| Credentials and destinations | External evaluation requires operator opt-in. The adapter uses a fixed literal path, pinned public IPv4 address, and TLS verification of the configured hostname before writing the bearer credential. Browser input, benchmark data, redirects and model replies cannot choose a destination. The new loopback TLS test uses a synthetic token and the adapter's real TLS context. The matching hostname receives the token. The mismatched hostname reaches the handshake but produces a sanitized failure with no second HTTP request. |
| Tool decisions and retries | The broker permits one read of the fixed synthetic ticket per case. It rejects unknown tools, invalid arguments, out-of-scope tickets, and duplicate reads. Bounded attempts and deadlines terminate malformed, unavailable, and slow responses. Controlled HTTP targets exercise timeouts, invalid framing and payloads, and retryable failures. |
| PostgreSQL and V1 | Workspace-scoped queries enforce isolation and the optional external profile is visible only to its configured workspace. The shared advisory lock controls admission. The additive migration preserves V1 evidence and records V2 run and case rows atomically. The API tests exercise upgrade, checksum drift, isolation, and rollback. |
| Protocol compatibility | The adapter enforces one non-streaming Chat Completions choice, native function calls or a strict JSON label, and a completion token cap. Provider-specific output shapes outside this subset can fail evaluation. |
| Scoring and release gate | Cases combine expected completion outcome, tool policy, and exact task label when applicable. Corrected and vulnerable references are evaluated on one dataset. V2 CI now requires a valid SHA-256 digest before comparing both reports. Injected faults have explicit trace markers. |
| Redaction and limits | Reports keep safe labels, fixed synthetic arguments, event kinds and measured counters. They omit raw provider payloads, prompts, unapproved arguments, and credentials. HTTP and API bodies, wire reads, attempts, concurrency and evaluator time are bounded. |

The certificate test substitutes loopback only at the socket destination boundary. It asserts the configured public pin and preserves SNI and hostname verification, so it cannot contact the named endpoint. It requires the OpenSSL command-line utility with `req -addext` support. CI checks for OpenSSL before running tests.

## Reproduce the checks

Use Python 3.12 with uv, Node.js 24 with npm, Docker with Compose, OpenSSL 1.1.1 or later, jq, actionlint, and Chromium for Playwright. Start a disposable PostgreSQL test database if you do not already have one. For Compose startup, configure the local workspace token first. [Getting started](getting-started.md)

```bash
docker run --rm -d --name agentproof-review-db -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=agentproof -e POSTGRES_PASSWORD=agentproof \
  -e POSTGRES_DB=agentproof_test postgres:17.10-bookworm
docker exec agentproof-review-db pg_isready -h 127.0.0.1 -U agentproof -d agentproof_test
export AGENTPROOF_TEST_DATABASE_URL=postgresql://agentproof:agentproof@127.0.0.1:5432/agentproof_test
```

```bash
uv sync --locked
uv run --locked ruff check backend
uv run --locked ruff format --check backend
uv run --locked mypy
openssl version
uv run --locked pytest
uv export --locked --no-emit-project --format requirements-txt -o /tmp/agentproof-review-requirements.txt
uv run --locked pip-audit --require-hashes --disable-pip -r /tmp/agentproof-review-requirements.txt
uv build --wheel --out-dir /tmp/agentproof-review-wheel
actionlint .github/workflows/ci.yml
```

```bash
cd frontend
npm ci
npm audit --audit-level=high
npm run lint
npm run typecheck
npm run build
npx playwright install chromium
cd ..
```

```bash
docker compose up --build -d --wait
curl --fail http://127.0.0.1:8000/healthz
docker compose exec -T api python -m agentproof.migrate
uv run --locked agentproof gate examples/defensive-suite.json --fixture corrected
uv run --locked agentproof gate examples/defensive-suite.json --fixture vulnerable
uv run --locked agentproof gate examples/retry-recovery.yaml
docker compose exec -T api agentproof model-gate --agent corrected
docker compose exec -T api agentproof model-gate --agent vulnerable
AGENTPROOF_E2E_TOKEN="$AGENTPROOF_DEMO_TOKEN" npm --prefix frontend run test:e2e
```

Both vulnerable gate commands should exit `1`. The corrected gates and V1 retry-recovery example should exit `0`. On the managed test host, the Compose build used `-f compose.yaml -f compose.trusted-ca.yaml` and the host's trusted CA bundle. Browser checks used `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium`. The Docker context exclusion check used disposable synthetic dotenv and PEM fixtures, and the wheel inspection checked that both SQL migrations and the benchmark were packaged. [Existing V2 benchmark measurements](evidence.md#v2-http-agent-verification)

## Remaining limits

- Live-provider compatibility and billing are **UNVERIFIED**. The loopback TLS and HTTP tests use synthetic endpoints and credentials. A report's model identity and evidence label describe the configured target and transport. They cannot attest which model served a response.
- The local reference assumes a developer-controlled loopback service and trustworthy proxy-header configuration. Network enforcement, production identity, public ingress capacity, and container OS advisories were outside these checks.
- Opt-in requests and retry counts are bounded per evaluation. Provider billing quotas are a separate control. A client disconnect or proxy timeout can leave a dispatched API run active. Retrying can create a second run. A malformed opt-in configuration can temporarily make the authorized workspace's agent catalog unavailable. [API limits](api.md#v2-http-agent-evaluation) · [Provider protocol](v2-evaluation.md#optional-external-provider)
