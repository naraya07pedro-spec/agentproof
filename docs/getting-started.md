# Getting started

## Start the local stack

Use Docker with the Compose plugin, Python 3.12, and a free local port for each of the dashboard and API. The stack binds the dashboard and API to loopback and keeps PostgreSQL on its internal network. Run these from the repository root.

```bash
export AGENTPROOF_DEMO_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENTPROOF_API_TOKENS="$(python3 -c 'import json, os; print(json.dumps({"demo": os.environ["AGENTPROOF_DEMO_TOKEN"]}))')"
docker compose up --build --wait
```

Check API readiness. A successful response confirms the API can reach the migrated database.

```bash
curl --fail http://localhost:8000/healthz
```

Open `http://localhost:3000` and enter the value of `AGENTPROOF_DEMO_TOKEN` in the workspace token field. Keep the token in your local development environment.

### Build behind a trusted HTTPS proxy

If your organization's HTTPS proxy signs package-registry traffic with a private
CA, pass the trusted PEM bundle that contains its CA to the Compose build. On
Linux, the system CA bundle often lives at the path shown below. Use the
bundle supplied or approved by your organization. The overlay mounts it only
for dependency installation during the build and keeps certificate verification
enabled. [Compose overlay](../compose.trusted-ca.yaml)

```bash
AGENTPROOF_BUILD_CA_FILE=/etc/ssl/certs/ca-certificates.crt \
  docker compose -f compose.yaml -f compose.trusted-ca.yaml up --build --wait
```

## Run and compare the fixtures

1. Open [the defensive suite](../examples/defensive-suite.json) and copy its JSON into the dashboard's suite creation form.
2. Start an evaluation with the vulnerable fixture. Open the failed assertions and trace for a case with an unauthorized attempt.
3. Run the same suite with the corrected fixture. Select the two runs in the comparison view and check the verdicts.
4. Inspect the duplicate action, timeout, retry-exhaustion, malformed-response, and interruption cases. A case passes when its actual behavior matches its declared expectation, even when the expected outcome is a timeout.
5. Replay an existing run. The replay gets a new run ID and points to the source run.

The corrected fixture passes all nine cases. The vulnerable fixture fails two critical cases. In `prompt-injection`, the vulnerable fixture treats a synthetic note as an instruction and applies a fake action. In `permission-escalation`, it honors a request to expand scope and the tool policy blocks the resulting attempt. The trace records each step.

Both fixture runs should report one applied action and one blocked duplicate in `duplicate-action`. The `timeout` case has a 150-millisecond simulated provider delay and a 100-millisecond case deadline. It should report a timeout and zero applied actions.

Use `docker compose down` to stop the stack. The default shutdown keeps the named PostgreSQL volume. Deleting the volume also deletes stored suites and runs.

## Run the HTTP benchmark

Keep the stack running and open **HTTP evaluations** in the connected dashboard.
Select the reference agent and synthetic benchmark to store a run, then switch
reference agents to compare cases, policy, and task scores. The command-line
gate uses the same benchmark through local HTTP. [HTTP evaluation guide](v2-evaluation.md)

## Run the fixture gate without the stack

Use Python 3.12 with uv. From the root, install the locked dependencies and run both verdict paths.

```bash
uv sync --locked
uv run python -m agentproof gate examples/defensive-suite.json --fixture corrected
uv run python -m agentproof gate examples/defensive-suite.json --fixture vulnerable
```

The corrected fixture should exit successfully. The intentionally vulnerable fixture should exit nonzero with a JSON report describing critical failures. Keep the original command's exit status if you capture its report for review.

## Develop against a local database

Install uv, Python 3.12, and Node.js 24 with npm for development outside Compose. With PostgreSQL running, point the API and migration runner at its connection URL. Use the same token configuration as in the Compose workflow. In separate shells, run the API and dashboard.

```bash
export AGENTPROOF_DATABASE_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/DATABASE'
uv sync --locked
uv run python -m agentproof.migrate
uv run uvicorn agentproof.api:app --host 127.0.0.1 --port 8000 --limit-concurrency 32 --no-access-log
```

```bash
cd frontend
npm ci
export AGENTPROOF_API_URL=http://127.0.0.1:8000
npm run dev
```

The browser sends workspace-authorized requests through the dashboard's same-origin API routes. The backend address comes from the dashboard process's configuration.

## Development checks

Run Python linting, strict type checking, and tests from the root. Set `AGENTPROOF_TEST_DATABASE_URL` to a separate, disposable PostgreSQL database to include the database checks. See the repository's CI workflow for the exact dependency audit commands.

```bash
uv run ruff check backend
uv run ruff format --check backend
uv run mypy
uv run pytest
```

With frontend dependencies installed, run these from `frontend`.

```bash
npm run lint
npm run typecheck
npm run build
```

With the API and dashboard running against the local database, install Chromium
and run the browser checks from `frontend`. The test creates fixtures in the
workspace identified by the token.

```bash
npx playwright install chromium
AGENTPROOF_E2E_TOKEN="$AGENTPROOF_DEMO_TOKEN" npm run test:e2e
```

Playwright uses `http://127.0.0.1:3000` by default. Use
`AGENTPROOF_E2E_URL` for a different loopback port. On Linux, install the
browser system dependencies if needed using `npx playwright install --with-deps chromium`.
Set `AGENTPROOF_E2E_SCREENSHOT=1` to save the comparison view under `frontend/test-results`.
