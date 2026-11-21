# AgentProof

AgentProof evaluates agent behavior against versioned test suites. The deterministic workflow checks scripted agents against simulated tools and provider failures. The HTTP evaluation workflow exercises an open-source reference agent through a chat-completions endpoint, checks its proposed tool calls against policy, and scores its task answers against labeled synthetic examples.

Both workflows report failed assertions and sanitized traces. The reference HTTP agent is scripted, and the benchmark tools use synthetic data. The optional external adapter requires explicit operator configuration. Live-provider behavior is **UNVERIFIED**.

## Run locally

Install Docker with the Compose plugin and Python 3.12. From the repository root, generate a local workspace credential and start the stack.

```bash
export AGENTPROOF_DEMO_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENTPROOF_API_TOKENS="$(python3 -c 'import json, os; print(json.dumps({"demo": os.environ["AGENTPROOF_DEMO_TOKEN"]}))')"
docker compose up --build --wait
```

Open the dashboard at `http://localhost:3000` and enter the value of `AGENTPROOF_DEMO_TOKEN`. Create a suite using [the defensive fixture suite](examples/defensive-suite.json). Run both fixture variants, open a stored result to inspect its assertions and trace, and compare the runs. Replay a run to execute its stored suite again with the same fixture.

The CLI uses the same evaluator and can run independently of PostgreSQL, Docker, and any external provider. Install [uv](https://docs.astral.sh/uv/) and use Python 3.12.

```bash
uv sync --locked
uv run python -m agentproof gate examples/defensive-suite.json --fixture corrected
uv run python -m agentproof gate examples/defensive-suite.json --fixture vulnerable
```

The second gate command returns a nonzero status because the vulnerable fixture fails critical checks. Both commands emit a JSON report.

## Evaluate the reference HTTP agent

With the Compose stack running, run both variants from inside the API container so the CLI can reach the reference listener on loopback.

```bash
docker compose exec -T api agentproof model-gate --agent corrected
docker compose exec -T api agentproof model-gate --agent vulnerable
```

Both commands use the same benchmark. The corrected agent is the passing reference and the vulnerable agent is the expected-failure reference. Inspect each JSON report for the benchmark digest, evidence source, task measurements, policy verdict, and case results. To store results, use **HTTP evaluations** in the dashboard. [HTTP evaluation guide](docs/v2-evaluation.md)

## Documentation

- [Getting started](docs/getting-started.md) walks through the dashboard, CLI, and local development.
- [HTTP evaluation guide](docs/v2-evaluation.md) covers the synthetic benchmark, scoring, and provider setup.
- [Test suite specification](docs/suite-specification.md) describes the version 1 JSON and YAML contract.
- [Architecture](docs/architecture.md) describes evaluation, replay, and storage.
- [HTTP API](docs/api.md) documents workspace access and request examples.
- [Threat model](docs/threat-model.md) describes trust boundaries and residual risks.
- [External observation adapter](docs/http-adapter.md) documents the V1 read-only observation SDK.
- [Evidence and limitations](docs/evidence.md) defines what each class of evidence supports and tracks validation.

AgentProof is an early open-source MVP. Fixture results describe the supplied simulations. No provider credentials are needed for the deterministic tests.

[Apache License 2.0](LICENSE)
