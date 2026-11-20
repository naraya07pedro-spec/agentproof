# Imported trace inspection (Expert V2, first slice)

The `inspect-trace` command checks **versioned, bounded, metadata-only event records** against an explicit policy. This is the first hardening slice toward evaluating real-agent observations.

```bash
uv sync --locked
uv run --locked agentproof inspect-trace examples/imported-safe-trace.json
```

`policy_passed=true` means the **events supplied to the inspector** satisfied the declared local policy. The report always says `evidence_origin: "imported_unverified"` and `release_eligible: false`. The CLI **does not authenticate the event origin**, detect fabricated logs, connect to an LLM, or certify release safety. It must not replace the existing simulated release gate or count as a live-agent test.

## Schema

Use UTF-8 JSON only. Maximum file size: 256 KiB. Version must be `"1"`; 1–512 events. Unknown fields, duplicate JSON keys, NaN/Infinity, malformed IDs, and invalid cross-field combinations are rejected. A `run_completed` event must be last, must carry no tool metadata, and sequence values must match their 1-based positions.

Events:
- `tool_requested`: `call_id`, `tool`, and `scope`; counts attempted access even if later denied
- `tool_denied`: denial matching a previous request
- `approval_granted`: explicit approval matching a previous request
- `action_applied`: external effect matching an earlier request; duplicates blocked by the inspector's policy verdict
- `run_completed`: explicit final event

Policy:
- `allowed_tools` and `allowed_scopes` are explicit allowlists
- `approval_required_tools` is a subset of the allowed tools
- `max_tool_requests` and `max_applied_actions` bound admitted event counts

Failure findings use stable, content-free codes, for example `unauthorized_tool_attempt`, `unapproved_effect`, `duplicate_effect`, and `missing_completion`. The report includes a SHA-256 digest of canonicalized parsed trace data for **comparison only**, not authentication.

## Failure behavior

- Exit `0`: well-formed trace, policy passed (still **unverified**).
- Exit `1`: well-formed trace, policy failed.
- Exit `2`: trace missing, malformed, unsupported, or over limit.

Import data is hostile by default. Never include prompts, credentials, outputs, raw tool arguments, customer identifiers, or production data. The schema rejects raw content fields, but metadata can still be sensitive. Do not use an imported passing trace to clear a protected release gate.

## Next: authenticated evidence

Build a bounded read-only agent-side harness or signed collector recording trusted tool attempts, effects, and approvals, with freshness, nonce, sequence, workspace identity, signatures, tamper protection, redaction and revocation checks. Only then add an explicit policy-controlled release evidence tier. Test signatures, cross-workspace replay, key rotation, missing spans and incomplete traces before allowing this tier to satisfy a release gate.
