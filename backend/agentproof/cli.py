"""Release gates for deterministic fixtures and HTTP agent evaluations."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Literal

from agentproof.engine import evaluate
from agentproof.spec import load_suite


def model_gate(agent: str, port: int | None) -> int:
    from agentproof.agent_v2 import run_benchmark
    from agentproof.model_adapter import (
        ModelConfigurationError,
        external_adapter_from_environment,
        reference_adapter,
    )

    try:
        if agent == "external":
            if port is not None:
                raise ModelConfigurationError("Reference port is unavailable for external agents")
            adapter = external_adapter_from_environment()
        else:
            variant: Literal["corrected", "vulnerable"] = (
                "corrected" if agent == "corrected" else "vulnerable"
            )
            adapter = reference_adapter(variant, port=8000 if port is None else port)
        report = asyncio.run(run_benchmark(adapter))
    except (ModelConfigurationError, OSError, ValueError):
        print(json.dumps({"error": "model_gate_configuration_invalid", "gate_passed": False}))
        return 2

    payload = report.model_dump(mode="json")
    payload["agent_id"] = agent
    payload["gate_passed"] = report.critical_failed == 0
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["gate_passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentproof")
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate", help="Evaluate a suite and fail on critical failures")
    gate.add_argument("suite", type=Path)
    gate.add_argument("--fixture", choices=("corrected", "vulnerable"), default="corrected")
    model = commands.add_parser(
        "model-gate", help="Evaluate an HTTP agent on the bundled benchmark"
    )
    model.add_argument("--agent", choices=("corrected", "vulnerable", "external"), required=True)
    model.add_argument("--port", type=int, help="Loopback port of the local reference agent")
    args = parser.parse_args(argv)

    if args.command == "model-gate":
        return model_gate(args.agent, args.port)

    try:
        suite = load_suite(args.suite)
    except (OSError, ValueError):
        print(json.dumps({"error": "invalid_or_unreadable_suite", "gate_passed": False}))
        return 2

    fixture: Literal["corrected", "vulnerable"] = (
        "corrected" if args.fixture == "corrected" else "vulnerable"
    )
    report = evaluate(suite, fixture)
    payload = report.model_dump(mode="json")
    payload["gate_passed"] = not report.critical_failed
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["gate_passed"] else 1
