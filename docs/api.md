# HTTP API

The local API listens on port 8000. Configure `AGENTPROOF_API_TOKENS` as a JSON object whose keys are workspace identifiers and whose values are unique bearer tokens. Generate a separate high-entropy token for each workspace. The configuration accepts 1–64 workspace identifiers, each starting with a lowercase letter and containing up to 64 lowercase letters, digits, hyphens, or underscores. Tokens contain 24–128 ASCII letters, digits, hyphens, or underscores. API operations under `/v1` and `/v2` require `Authorization: Bearer <token>`. Cross-workspace and unknown-resource lookups return 404. `GET /healthz` is a readiness endpoint.

## V1 fixture evaluation

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /healthz` | None | `{"status":"ok"}` or HTTP 503 when storage is unavailable |
| `GET /v1/suites` | None | Object with a `suites` list |
| `POST /v1/suites` | JSON suite specification | Suite record including its validated specification |
| `GET /v1/suites/{suite_id}` | None | Suite record including its specification |
| `POST /v1/suites/{suite_id}/runs` | `{"fixture":"corrected"}` or `{"fixture":"vulnerable"}` | Stored run with case results |
| `GET /v1/runs` | None | Object with a `runs` list of run summaries |
| `GET /v1/runs/{run_id}` | None | Stored run with case results |
| `POST /v1/runs/{run_id}/replay` | None | Newly stored run linked to its source |

A suite summary includes `id`, `name`, and `created_at`. A run summary includes its ID, suite ID and name, fixture, creation timestamp, optional source ID in `replay_of`, evidence classification, suite and engine version, verdict, case count, failed count, and critical failure count. Run details add each case's verdict, outcome, assertion results, and ordered trace.

Suite and run POST requests require `Content-Type: application/json`. Suite bodies are limited to 128 KiB and run bodies to 1 KiB. Uploads have a five-second deadline. The dashboard proxy caps request bodies at 128 KiB. The CLI also accepts YAML. Suite and run lists default to 50 records newest first. Direct API clients can use `limit` (1–100) and `offset` (0–100,000) for pagination. IDs are UUIDs. The bundled simulation stores `deterministic_simulation` in the run's `evidence` field. The local server serves the generated OpenAPI schema as JSON at `/openapi.json`.

Run creation and replay return HTTP 201 for stored results, regardless of the evaluation verdict. Only one evaluation can hold the run slot per PostgreSQL database at a time. Concurrent starts may return HTTP 429 with `Retry-After: 1`. An incompatible engine version or a replay that differs from its source returns HTTP 409.

### Fixture example

Create a suite and run the corrected fixture using the token from the local setup. These commands require curl and jq and run from the repository root.

```bash
suite_id=$(curl -fsS http://localhost:8000/v1/suites \
  -H "Authorization: Bearer ${AGENTPROOF_DEMO_TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary @examples/defensive-suite.json | jq -er .id)

curl -fsS "http://localhost:8000/v1/suites/${suite_id}/runs" \
  -H "Authorization: Bearer ${AGENTPROOF_DEMO_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"fixture":"corrected"}'
```

A successful run-creation response means that the report was stored. Read `passed`, `critical_failed`, and the case results to determine the evaluation outcome.

## V2 HTTP agent evaluation

The API runs the built-in reference agents against the curated synthetic benchmark. Agent and suite selection uses catalog IDs. An explicitly configured external model is visible only to the operator-selected workspace. Hostname, path, model and secret configuration belong to the operator process. Requests accept only the agent and suite IDs. [Provider configuration](v2-evaluation.md#optional-external-provider)

| Method and path | Request | Response |
| --- | --- | --- |
| `GET /v2/agents` | None | Object with the configured `agents` catalog |
| `GET /v2/suites` | None | Object with the installed benchmark `suites` catalog |
| `POST /v2/runs` | JSON with `agent_id` and `suite_id` | Newly stored report, HTTP 201 |
| `GET /v2/runs` | None | Object with a workspace-scoped `runs` list |
| `GET /v2/runs/{run_id}` | None | Stored report with metrics and case results |

Agents expose identity, description, model, version, and evidence mode. Suites expose identity, description, version, and a dataset digest. A run summary includes its own ID and correlation ID, agent and model identity, suite version and digest, adapter and evaluator versions, evidence source, timestamps, verdict and case counts, and optional cost. Run detail adds task and policy measurements and case results. List pagination follows the V1 limits. A run start with an unknown suite or unavailable agent returns HTTP 404. The shared database evaluation slot can return HTTP 429 with `Retry-After: 1`. The JSON request accepts at most 1 KiB.

### Reference HTTP example

Use the local workspace token and select the curated benchmark by its returned ID.

```bash
benchmark_id=$(curl -fsS http://127.0.0.1:8000/v2/suites \
  -H "Authorization: Bearer ${AGENTPROOF_DEMO_TOKEN}" | jq -er '.suites[0].id')

request=$(jq -cn --arg id "$benchmark_id" \
  '{agent_id:"reference-corrected",suite_id:$id}')

curl -fsS http://127.0.0.1:8000/v2/runs \
  -H "Authorization: Bearer ${AGENTPROOF_DEMO_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "$request"
```

An HTTP 201 acknowledges that a report was recorded. Evaluate its verdict and per-case measurements to decide whether a release should proceed. Use the same benchmark ID and compare the returned dataset digest when comparing model runs.
