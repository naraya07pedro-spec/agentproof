# PR #1 pre-merge engineering and security review

Reviewed GitHub `main` at `8c4758d7d81070cb0672f56fb7e9258e124d34a8` against PR #1 as opened at `d7808274dd28bb6b90c8108b6cfaf58e4b850cba`. Review fixes are in `75d12c86f937a055fd89c594f196df460a76c36f`. This report is a documentation-only follow-up. The merge recommendation applies to the documented local MVP.

## Blocking findings fixed

| Severity | Finding | Resolution and verification |
| --- | --- | --- |
| High | The default Swagger and ReDoc pages loaded mutable third-party browser scripts. Swagger ran those scripts on the local API origin and accepted workspace bearer tokens. | Disabled the interactive pages and OAuth redirect. Kept the generated `/openapi.json` contract. The API test and live Compose check returned 404 for the three HTML routes and 200 for the OpenAPI JSON with the bearer scheme. |
| High | Docker's former ignore rules missed nested dotenv files. The dashboard Dockerfile copied all of `frontend/` into the final image, which could include local credentials. | Restricted the build context to application source, configuration, package manifests, and lockfiles, and narrowed both Dockerfiles' copies. A BuildKit test using only synthetic dotenv and PEM files confirmed the prior context included both and the new context excludes both while retaining application source and migrations. |

The findings describe credential exposure paths. The review used synthetic credentials and found no evidence of a credential disclosure.

## Other review results

- API resource operations use bound SQL parameters and workspace-scoped lookups. Run and replay foreign keys preserve workspace and suite ownership. Tests cover cross-workspace access, serialization through the evaluation slot, and rollback of partial writes.
- The migration runner uses a transactional advisory lock, ordered migration files, and stored checksums. Verification covered migration on a fresh database, a repeat invocation, checksum drift rejection, and SQL inclusion in the Python wheel.
- The same-origin proxy uses a server-configured upstream, exact route allowlisting, redirect rejection, and bounded uploads and request time. The production build rendered stored runs. Browser tests covered invalid proxy routes, absent credentials, oversized bodies, and incomplete uploads.
- The release gate returned 0 for the corrected fixture and 1 for the vulnerable fixture, which failed two critical assertions. GitHub Actions uses SHA-pinned actions, read-only repository permissions, and locked installs.

## Local verification

The host used Linux x86-64, Python 3.12.14, uv 0.13.6, Node.js 24.22.1, npm 11.9.0, PostgreSQL 17.10, Docker 29.9.2, Compose 2.40.3, and Chromium 156.0.8078.79. Use the local database, token setup, and trusted CA overlay in the getting-started guide. [Getting started](getting-started.md)

| Checks | Result |
| --- | --- |
| `uv sync --locked`, `uv run --locked ruff check backend`, `uv run --locked ruff format --check backend`, `uv run --locked mypy` | Passed. |
| `uv run --locked pytest` with a disposable PostgreSQL database | 54 passed. |
| `uv build --wheel`, inspection of wheel contents, `actionlint .github/workflows/ci.yml`, and `git diff --check` | Passed. |
| `uv export --locked --no-emit-project --format requirements-txt`, then `pip-audit --require-hashes --disable-pip` with the exported file, and `npm audit --audit-level=high` | No known vulnerabilities reported by the package advisory sources. |
| `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build` | Passed. |
| `docker compose -f compose.yaml -f compose.trusted-ca.yaml up --build --wait`, API readiness and routes, and `python -m agentproof.migrate` inside the running API container | Passed. PostgreSQL was initialized on a fresh volume. A second migration invocation confirmed the schema was current. |
| `npm --prefix frontend run test:e2e` against Compose | 3 passed using local Chromium. |
| `uv run --locked agentproof gate examples/defensive-suite.json --fixture corrected` and the same command with `--fixture vulnerable`; `uv run --locked agentproof gate examples/retry-recovery.yaml` | Corrected 9/9, exit 0. Vulnerable 7/9, exit 1. YAML example 1/1, exit 0. |

## Limitations and follow-up

The deterministic suite verifies scripted fixture behavior. The HTTP adapter tests use mocked transport. Live-provider operation, real hostile-code isolation, production deployment, repository branch-protection rules, and OS-level container advisories were outside this review's verification. The local dashboard and API rely on loopback binding and operator-controlled Docker; a public deployment needs separate identity, quota, ingress, and egress controls.

One nonblocking CLI robustness issue remains. Some malformed explicitly tagged YAML can bypass the normalized invalid-suite error and emit a traceback. The API only accepts JSON. Continue using synthetic input for CLI fixtures. [Threat model](threat-model.md)
