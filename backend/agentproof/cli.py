"""Local release gate for versioned deterministic fixture suites."""

import argparse
import json
from pathlib import Path
from typing import Literal

from agentproof.engine import evaluate
from agentproof.spec import load_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentproof")
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate", help="Evaluate a suite and fail on critical failures")
    gate.add_argument("suite", type=Path)
    gate.add_argument("--fixture", choices=("corrected", "vulnerable"), default="corrected")
    args = parser.parse_args(argv)

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
