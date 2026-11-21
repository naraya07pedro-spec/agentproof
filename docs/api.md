# HTTP API

The local API listens on port 8000. Configure `AGENTPROOF_API_TOKENS` as a JSON object whose keys are workspace identifiers and whose values are unique bearer tokens. Generate a separate high-entropy token for each workspace. The configuration accepts 1–64 workspace identifiers, each starting with a lowercase letter and containing up to 64 lowercase letters, digits, hyphens, or underscores. Tokens contain 24–128 ASCII letters, digits, hyphens, or underscores. API operations under `/v1` require `Authorization: Bearer <token>`. Cross-workspace and unknown-resource lookups return 404. `GET /healthz` is a readiness endpoint.

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

## Example

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
