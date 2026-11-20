"""Recorded evidence inspection: policy failures and honest provenance labels."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pytest import CaptureFixture

from agentproof.cli import main
from agentproof.trace_inspect import RecordedTrace, TraceInspection, inspect_trace, load_trace


def example_events() -> list[dict[str, Any]]:
    return [
        {"seq": 1, "kind": "tool_requested", "call_id": "c1", "tool": "record", "scope": "test"},
        {"seq": 2, "kind": "approval_granted", "call_id": "c1", "tool": "record", "scope": "test"},
        {"seq": 3, "kind": "action_applied", "call_id": "c1", "tool": "record", "scope": "test"},
        {"seq": 4, "kind": "run_completed"},
    ]


def example_trace() -> dict[str, Any]:
    return {
        "version": "1",
        "run_id": "test-run",
        "policy": {
            "allowed_tools": ["record"],
            "allowed_scopes": ["test"],
            "approval_required_tools": ["record"],
            "max_tool_requests": 1,
            "max_applied_actions": 1,
        },
        "events": example_events(),
    }


def evaluate(value: dict[str, Any]) -> TraceInspection:
    return inspect_trace(RecordedTrace.model_validate(value))


def test_valid_imported_trace_is_not_release_eligible() -> None:
    result = evaluate(example_trace())
    assert result.policy_passed is True
    assert result.findings == []
    assert result.evidence_origin == "imported_unverified"
    assert result.release_eligible is False
    assert len(result.run_digest_sha256) == 64


def test_unapproved_effect_fails_closed() -> None:
    value = example_trace()
    value["events"] = [example_events()[0], example_events()[2], example_events()[3]]
    value["events"][1]["seq"] = 2
    value["events"][2]["seq"] = 3
    result = evaluate(value)
    assert result.policy_passed is False
    assert "unapproved_effect" in [finding.code for finding in result.findings]


def test_permission_escalation_detected_even_if_tool_denied() -> None:
    value = example_trace()
    value["events"] = [
        {"seq": 1, "kind": "tool_requested", "call_id": "x", "tool": "admin", "scope": "prod"},
        {"seq": 2, "kind": "tool_denied", "call_id": "x", "tool": "admin", "scope": "prod"},
        {"seq": 3, "kind": "run_completed"},
    ]
    result = evaluate(value)
    assert {"unauthorized_tool_attempt", "unauthorized_scope_attempt"}.issubset(
        {finding.code for finding in result.findings}
    )


def test_duplicate_effect_and_missing_completion_fail() -> None:
    value = example_trace()
    value["events"] = example_events()[:3] + [
        {"seq": 4, "kind": "action_applied", "call_id": "c1", "tool": "record", "scope": "test"}
    ]
    result = evaluate(value)
    assert {"duplicate_effect", "effect_budget_exceeded", "missing_completion"}.issubset(
        {finding.code for finding in result.findings}
    )


def test_orphan_approval_and_effect_fail() -> None:
    value = example_trace()
    value["events"] = [
        {"seq": 1, "kind": "approval_granted", "call_id": "c1", "tool": "record", "scope": "test"},
        {"seq": 2, "kind": "action_applied", "call_id": "c1", "tool": "record", "scope": "test"},
        {"seq": 3, "kind": "run_completed"},
    ]
    assert {"orphan_approval", "unmatched_effect"}.issubset(
        {finding.code for finding in evaluate(value).findings}
    )


def test_strict_schema_rejects_content_and_noncontiguous_trace() -> None:
    value = example_trace()
    value["events"][0]["raw_prompt"] = "secret"
    with pytest.raises(ValidationError):
        evaluate(value)

    value = example_trace()
    value["events"][1]["seq"] = 9
    assert "sequence_gap" in [finding.code for finding in evaluate(value).findings]


def test_parser_rejects_duplicate_keys_and_overlimit(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"version":"1","version":"1"}')
    with pytest.raises(ValueError):
        load_trace(invalid)
    invalid.write_bytes(b" " * 262145)
    with pytest.raises(ValueError):
        load_trace(invalid)


def test_cli_exit_code_and_sanitized_error(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(example_trace()))
    assert main(["inspect-trace", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["policy_passed"] is True
    assert result["release_eligible"] is False

    path.write_text('{"untrusted": "do not expose me"}')
    assert main(["inspect-trace", str(path)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error": "invalid_or_unreadable_trace",
        "policy_passed": False,
    }
