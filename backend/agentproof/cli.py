"""Local release gate for versioned deterministic fixture suites."""

import argparse
import json
from pathlib import Path
from typing import Literal

from agentproof.engine import evaluate
from agentproof.spec import load_suite
from agentproof.trace_inspect import inspect_trace, load_policy, load_trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentproof")
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate", help="Evaluate a suite and fail on critical failures")
    gate.add_argument("suite", type=Path)
    gate.add_argument("--fixture", choices=("corrected", "vulnerable"), default="corrected")
    trace = commands.add_parser("inspect-trace", help="Inspect unverified imported trace events")
    trace.add_argument("trace", type=Path)
    trace.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "inspect-trace":
        try:
            recorded = load_trace(args.trace)
            trusted_policy = load_policy(args.policy)
        except (OSError, ValueError):
            print(json.dumps({"error": "invalid_or_unreadable_trace", "policy_passed": False}))
            return 2
        inspected = inspect_trace(recorded, trusted_policy)
        print(json.dumps(inspected.model_dump(mode="json"), sort_keys=True))
        return 0 if inspected.policy_passed else 1

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
