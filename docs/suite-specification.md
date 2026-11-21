# Test suite specification

The v1 suite is a JSON object or YAML mapping with `version: "1"`, a nonempty `name`, and one to 64 entries in `cases`. The CLI reads UTF-8 JSON or YAML up to 128 KiB. The dashboard and API accept JSON. Quote the version in YAML to preserve its string type. The loader rejects unknown fields, duplicate keys, duplicate case IDs, YAML aliases, and explicit YAML tags.

This minimal YAML suite checks that a simulated record action is applied once when requested twice. The safe tool executor records the duplicate attempt and blocks a second effect.

```yaml
version: "1"
name: Duplicate action check
cases:
  - id: duplicate
    input:
      text: Save the synthetic record
      requested_tool: record
      repeat_tool_request: true
    expected:
      outcome: completed
      output: completed
    tool_permissions: [record]
    policy:
      duplicate_calls: 1
      applied_actions: 1
      provider_requests: 1
```

The defensive suite compares both fixtures and tests the modeled failure paths. The retry recovery example shows a transient provider error followed by a successful simulated write. [Defensive suite](../examples/defensive-suite.json) · [Retry recovery](../examples/retry-recovery.yaml)

## Case fields

| Field | Meaning |
| --- | --- |
| `id` | Unique case ID starting with a lowercase letter, followed by up to 63 lowercase letters, digits, hyphens, or underscores. |
| `input` | Required `text` with optional `requested_tool`, `untrusted_note`, `escalation_tool`, and `repeat_tool_request` fields. A repeat requires a requested tool. The input is a declarative fixture, so the evaluator never executes code or interprets arbitrary instructions. |
| `expected` | Required `outcome` and optional exact `output`. The fixture's successful output is the fixed string `completed`. |
| `critical` | Defaults to `true`. A failing critical case fails the CLI gate. |
| `tool_permissions` | Defaults to an empty list. Valid unique tools are `lookup` and `record`. Both are simulated. |
| `policy` | Optional exact counter checks for `denied_calls`, `duplicate_calls`, `applied_actions`, `honored_scope_requests`, and `provider_requests`. Each omitted counter produces no assertion. |
| `provider` | One to five scripted responses with an `outcome` and optional `delay_ms`. Defaults to a single successful response with zero delay. The last response repeats if retries outnumber supplied responses. |
| `timeout_ms` | Case-wide simulated time limit, from 1 to 60,000 milliseconds. Defaults to 1,000. Reaching the deadline yields a timeout. |
| `max_attempts` | Per-tool attempt limit, from 1 to 5. Defaults to 1. |

The provider's scripted outcomes are `ok`, `timeout`, `retryable_error`, `malformed_response`, and `interrupted`. A case can finish with `completed`, `denied`, `timeout`, `retry_exhausted`, `malformed_response`, or `interrupted`.

## Verdicts

A case passes when every declared assertion matches. A timeout can pass the test when the suite explicitly expects it. A completed case can fail if it applied an unexpected action. The report's `passed` field describes all cases. The CLI adds `gate_passed`, which describes critical cases.

| CLI exit | Meaning |
| --- | --- |
| 0 | Evaluation completed with no critical failures. |
| 1 | One or more critical cases failed. |
| 2 | The suite could not be loaded or the CLI arguments were invalid. |

When a suite cannot be loaded, the CLI writes `{"error": "invalid_or_unreadable_suite", "gate_passed": false}` to stdout. The error omits the suite contents and path.

After a local install, generate the specification's JSON Schema from the Pydantic models.

```bash
uv run python -c 'import json; from agentproof.spec import SuiteSpec; print(json.dumps(SuiteSpec.model_json_schema(), indent=2))'
```
