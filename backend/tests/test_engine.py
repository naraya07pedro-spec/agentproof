from pathlib import Path

import pytest
from pydantic import ValidationError

from agentproof.engine import evaluate
from agentproof.spec import SuiteSpec, load_suite, parse_suite_json

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_both_fixtures_run_the_same_suite() -> None:
    suite = load_suite(EXAMPLES / "defensive-suite.json")
    corrected = evaluate(suite, "corrected")
    vulnerable = evaluate(suite, "vulnerable")

    assert len(corrected.results) == 9
    assert corrected.passed and corrected.critical_failed == 0
    assert not vulnerable.passed and vulnerable.critical_failed == 2
    assert {result.case_id for result in vulnerable.results if not result.passed} == {
        "prompt-injection",
        "permission-escalation",
    }
    by_case = {result.case_id: result for result in vulnerable.results}
    injection = by_case["prompt-injection"]
    assert any(event.kind == "action_applied" for event in injection.trace)
    escalation = by_case["permission-escalation"]
    assert any(event.kind == "tool_denied" for event in escalation.trace)
    assert not any(event.kind == "provider_requested" for event in escalation.trace)
    assert evaluate(suite, "corrected").model_dump_json() == corrected.model_dump_json()


def test_provider_failure_and_duplicate_traces() -> None:
    report = evaluate(load_suite(EXAMPLES / "defensive-suite.json"), "corrected")
    results = {result.case_id: result for result in report.results}
    assert [event.kind for event in results["duplicate-action"].trace].count("action_applied") == 1
    assert any(event.kind == "duplicate_blocked" for event in results["duplicate-action"].trace)
    assert results["timeout"].outcome == "timeout"
    assert results["timeout"].trace[-1].elapsed_ms == 100
    retry_attempts = [
        event.attempt
        for event in results["retry-exhaustion"].trace
        if event.kind == "provider_requested"
    ]
    assert retry_attempts == [1, 2, 3]
    assert results["retry-exhaustion"].outcome == "retry_exhausted"
    assert results["malformed-response"].outcome == "malformed_response"
    assert results["worker-interruption"].outcome == "interrupted"
    for result in report.results:
        assert [event.seq for event in result.trace] == list(range(1, len(result.trace) + 1))
        elapsed_ms = [event.elapsed_ms for event in result.trace]
        assert elapsed_ms == sorted(elapsed_ms)
    for case_id in ("timeout", "worker-interruption", "unauthorized-tool"):
        assert not any(event.kind == "action_applied" for event in results[case_id].trace)


def test_yaml_recovery_and_shared_virtual_timeout() -> None:
    suite = load_suite(EXAMPLES / "retry-recovery.yaml")
    result = evaluate(suite, "corrected").results[0]
    assert result.passed
    assert result.trace[-1].elapsed_ms == 70

    case = suite.cases[0].model_copy(update={"timeout_ms": 70})
    timed_out = evaluate(suite.model_copy(update={"cases": [case]}), "corrected").results[0]
    assert not timed_out.passed
    assert timed_out.outcome == "timeout"
    assert timed_out.trace[-1].elapsed_ms == 70
    assert not any(event.kind == "action_applied" for event in timed_out.trace)


def test_reports_do_not_include_input_or_expected_output() -> None:
    source = """
    {
      "version":"1",
      "name":"sensitive-suite-name",
      "cases":[{"id":"one","critical":false,
      "input":{"text":"sensitive-prompt","untrusted_note":"sensitive-note"},
      "expected":{"outcome":"completed","output":"sensitive-output"}}]
    }
    """
    report = evaluate(parse_suite_json(source), "corrected")
    assert not report.passed
    assert report.critical_failed == 0
    assert report.results[0].assertions[-1].actual is False
    assert "sensitive-" not in report.model_dump_json()


def test_strict_validation() -> None:
    tests: list[dict[str, object]] = [
        {"version": 1},
        {"version": "2"},
        {"cases": []},
        {"cases": [{"id": "case", "input": {"text": "t"}, "expected": {"outcome": "x"}}]},
        {
            "cases": [
                {
                    "id": "case",
                    "input": {"text": "t"},
                    "expected": {"outcome": "completed"},
                    "timeout_ms": True,
                }
            ]
        },
        {
            "cases": [
                {
                    "id": "case",
                    "input": {"text": "t", "repeat_tool_request": True},
                    "expected": {"outcome": "completed"},
                }
            ]
        },
        {
            "cases": [
                {
                    "id": "case",
                    "input": {"text": "t"},
                    "expected": {"outcome": "completed"},
                    "tool_permissions": ["record", "record"],
                }
            ]
        },
    ]
    suite = load_suite(EXAMPLES / "defensive-suite.json")
    for update in tests:
        with pytest.raises(ValidationError):
            SuiteSpec.model_validate(suite.model_dump() | update)

    with pytest.raises(ValidationError, match="case ids must be unique"):
        SuiteSpec.model_validate(suite.model_dump() | {"cases": [suite.cases[0].model_dump()] * 2})


@pytest.mark.parametrize("invalid_text", ["NUL\x00", "unpaired\ud800"])
def test_specification_rejects_text_incompatible_with_jsonb(invalid_text: str) -> None:
    suite = load_suite(EXAMPLES / "defensive-suite.json")
    with pytest.raises(ValidationError):
        SuiteSpec.model_validate(suite.model_dump() | {"name": invalid_text})
    case = suite.cases[0].model_dump() | {"input": {"text": invalid_text}}
    with pytest.raises(ValidationError):
        SuiteSpec.model_validate(suite.model_dump() | {"cases": [case]})


def test_loaders_reject_duplicate_keys_and_unbounded_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_suite_json('{"version":"1","version":"2"}')
    with pytest.raises(ValueError, match="128 KiB"):
        parse_suite_json(b" " * (128 * 1024 + 1))
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_suite_json(b"\xff")

    path = tmp_path / "invalid.yaml"
    path.write_text('version: "1"\nversion: "2"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_suite(path)
    path.write_text("version: &version '1'\nname: *version\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aliases"):
        load_suite(path)
