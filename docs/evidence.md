# Evidence and limitations

| Evidence class | Meaning |
| --- | --- |
| Deterministic simulation | Scripted fixture, fake tools and provider responses, stable time and trace data. An API run created from the bundled suite is stored with `evidence: deterministic_simulation`. |
| Integration test | A real component boundary is exercised, such as the API talking to PostgreSQL or the browser driving the dashboard. This describes the verification method, while the agent behavior in that workflow is still simulated. |
| Real provider test | An evaluation backed by a real external provider, with its version, endpoint, credentials scope, and environmental dependencies identified. The deterministic workflow uses no such provider. |
| Actual production use | Observed use in a deployed system with a documented operational context. Local and CI validation do not establish this status. |

The demo shows how assertion failures and traces explain a fixture outcome. It provides no benchmark for model reliability, attack resistance, or production capacity. Simulated time and interruption are reproducible inputs. They do not measure service latency or guarantee recovery after a process kill.

## Acceptance evidence

Use [the local commands](getting-started.md) to reproduce the demo. A verification record should include the exact checked-out revision, commands and results, whether PostgreSQL and a browser were available, and any skipped or unavailable checks. A passing local check establishes local evidence, even if a hosted CI result is unavailable.

## Local verification record

Implementation revision `4121694b59c302fc66c58e6ab9b691f0f7e69cb4`. This report and its screenshot are tracked in a later documentation commit. The verification host ran Linux x86-64, Python 3.12.14, uv 0.13.6, Node.js 24.22.1, npm 11.9.0, Docker 29.9.2 with Compose 2.40.3, PostgreSQL 17.10, and system Chromium 156.0.8078.79.

| Command | Observed result |
| --- | --- |
| `uv sync --locked` | Installed the locked Python environment. |
| `uv run --locked ruff check backend`, `uv run --locked ruff format --check backend`, `uv run --locked mypy` | Passed. Strict mypy checked 13 Python files. |
| `uv run --locked pytest` with `AGENTPROOF_TEST_DATABASE_URL` set to a disposable PostgreSQL database | 53 passed, including 10 API/database tests, 8 engine tests, 2 CLI tests, and 33 mocked-transport HTTP adapter tests. |
| `uv run --locked python -m agentproof.migrate` twice | Applied the initial migration on a fresh database and confirmed repeatability. The database tests also verified checksum drift rejection. |
| `uv build --wheel --out-dir /tmp/agentproof-wheel` | Built the wheel and confirmed it contains the initial SQL migration. |
| `uv export --locked --no-emit-project --format requirements-txt -o /tmp/agentproof-locked-requirements.txt` followed by `uv run --locked pip-audit --cache-dir /tmp/agentproof-audit --require-hashes --disable-pip -r /tmp/agentproof-locked-requirements.txt` | No known vulnerabilities reported by the audit source at check time. |
| `npm ci`, `npm audit --audit-level=high`, `npm run lint`, `npm run typecheck`, `npm run build` in `frontend` | Passed. npm audit reported zero vulnerabilities. ESLint remains on the pinned major supported by the current Next.js lint plugins and emits an upstream support warning. |
| `actionlint .github/workflows/ci.yml` with actionlint 1.7.9 | Passed locally. |

### Release gate evidence

```bash
uv run --locked agentproof gate examples/defensive-suite.json --fixture corrected
uv run --locked agentproof gate examples/defensive-suite.json --fixture vulnerable
uv run --locked agentproof gate examples/retry-recovery.yaml
```

The corrected fixture exited 0 with 9 of 9 cases passing. The vulnerable fixture exited 1 with 7 of 9 passing. Its critical failures were `prompt-injection` and `permission-escalation`. The YAML retry-recovery example exited 0 with its one case passing. The commands produced machine-readable JSON.

To exercise a regression in the corrected release-gate path, I used a temporary copy of the defensive JSON suite, set `cases[0].critical` to `true` and `cases[0].expected.outcome` to `malformed_response`, and ran `uv run --locked agentproof gate /tmp/agentproof-regression.json --fixture corrected`. The process exited 1 and emitted `gate_passed: false` with one critical failure. The GitHub Actions job uses the corrected gate as a failing step when its command exits nonzero. Hosted workflow execution and repository required-check rules still need verification.

### Compose and browser integration

The cloud host uses a private HTTPS proxy CA for package installation. With a disposable workspace token configured, I ran the following commands. The proxy overlay preserves certificate verification and only supplies the build-time trust bundle.

```bash
AGENTPROOF_BUILD_CA_FILE=/etc/ssl/certs/ca-certificates.crt \
  docker compose -p agentproof-review -f compose.yaml -f compose.trusted-ca.yaml \
  up --build --wait --wait-timeout 90
curl --fail http://127.0.0.1:8000/healthz
AGENTPROOF_E2E_SCREENSHOT=1 AGENTPROOF_E2E_TOKEN="$AGENTPROOF_DEMO_TOKEN" \
  PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm --prefix frontend run test:e2e
```

The API returned `{"status":"ok"}` and the dashboard returned HTTP 200. All three Playwright checks passed. The browser created a suite, ran both fixtures, inspected a failed assertion and trace, compared the stored runs, replayed a run, and reloaded it from PostgreSQL. The other checks covered an unsupported proxy route, a missing token, an oversized upload, and an incomplete upload that reached its deadline. The three stored run IDs matched before and after `docker compose -p agentproof-review restart db api` and a successful readiness check.

[Dashboard screenshot from the stored-run browser test](stored-run-comparison.png)

The integration suite used a real PostgreSQL database and a local browser. External adapter tests used a mocked transport. No live-provider validation, hostile-code isolation assessment, deployment, or production observation was performed.
