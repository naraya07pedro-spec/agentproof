# AgentProof

AgentProof evaluates agent behavior against versioned test suites. Its first workflow runs scripted fixture agents against simulated tools and providers, records policy decisions and failures, and produces a deterministic release-gate result.

The same defensive suite exercises two fixtures. The corrected fixture ignores untrusted instructions and rejects requests to expand its scope. The simulated tool executor checks permissions and prevents repeated effects in both variants. The intentionally flawed fixture demonstrates how policy assertions catch failures even when the simulator prevents a forbidden action from taking effect.

## Run locally

Install Docker with the Compose plugin and Python 3.12. From the repository root, generate a local workspace credential and start the stack.

```bash
export AGENTPROOF_DEMO_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENTPROOF_API_TOKENS="$(python3 -c 'import json, os; print(json.dumps({"demo": os.environ["AGENTPROOF_DEMO_TOKEN"]}))')"
docker compose up --build
```

Open the dashboard at `http://localhost:3000` and enter the value of `AGENTPROOF_DEMO_TOKEN`. Create a suite using [the defensive fixture suite](examples/defensive-suite.json). Run both fixture variants, open a stored result to inspect its assertions and trace, and compare the runs. Replay a run to execute its stored suite again with the same fixture.

The CLI uses the same evaluator and can run independently of PostgreSQL, Docker, and any external provider. Install [uv](https://docs.astral.sh/uv/) and use Python 3.12.

```bash
uv sync --locked
uv run python -m agentproof gate examples/defensive-suite.json --fixture corrected
uv run python -m agentproof gate examples/defensive-suite.json --fixture vulnerable
```

The second gate command returns a nonzero status because the vulnerable fixture fails critical checks. Both commands emit a JSON report.

## Inspect recorded agent traces (V2 preview)

The first expert-tier enhancement inspects bounded, metadata-only imported event traces against a strict default-deny policy:

```bash
uv run --locked agentproof inspect-trace examples/imported-safe-trace.json
uv run --locked agentproof inspect-trace examples/imported-unsafe-trace.json
```

These commands respectively return exit codes 0 and 1. The imported trace origin is **unverified** and is always marked `release_eligible: false`. A passing inspection does not certify a real agent, its logs, or a production release. See [recorded trace inspection](docs/recorded-trace.md).

## Documentation

- [Getting started](docs/getting-started.md) walks through the dashboard, CLI, and local development.
- [Test suite specification](docs/suite-specification.md) describes the version 1 JSON and YAML contract.
- [Architecture](docs/architecture.md) describes evaluation, replay, and storage.
- [HTTP API](docs/api.md) documents workspace access and request examples.
- [Threat model](docs/threat-model.md) describes trust boundaries and residual risks.
- [External agent adapter](docs/http-adapter.md) documents the separate, opt-in HTTP integration.
- [Recorded trace inspection](docs/recorded-trace.md) describes untrusted imported evidence and fail-closed checks.
- [Evidence and limitations](docs/evidence.md) defines what each class of evidence supports and tracks validation.

AgentProof is an early open-source MVP. Fixture results describe the supplied simulations. No provider credentials are needed for the deterministic tests.

[Apache License 2.0](LICENSE)
