"""Pure deterministic fixture evaluation with a virtual clock and sanitized traces."""

from dataclasses import dataclass, field
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

from agentproof.spec import CaseSpec, Outcome, SuiteSpec, ToolName

FixtureName = Literal["corrected", "vulnerable"]
ENGINE_VERSION = "1.0.0"
FIXTURE_NAMES = get_args(FixtureName)
# This marker only controls the deliberately permissive toy agent in this module.
FIXTURE_RECORD_MARKER = "[fixture:perform-record]"
EventKind = Literal[
    "case_started",
    "untrusted_note_ignored",
    "untrusted_note_followed",
    "scope_request_ignored",
    "scope_request_honored",
    "tool_requested",
    "tool_denied",
    "duplicate_blocked",
    "provider_requested",
    "provider_retryable_error",
    "provider_succeeded",
    "action_applied",
    "timed_out",
    "retry_exhausted",
    "malformed_response",
    "worker_interrupted",
    "case_finished",
]
AssertionValue = int | bool | Outcome


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TraceEvent(ReportModel):
    seq: int
    kind: EventKind
    elapsed_ms: int
    tool: ToolName | None = None
    attempt: int | None = None


class Assertion(ReportModel):
    check: str
    passed: bool
    expected: AssertionValue
    actual: AssertionValue


class CaseResult(ReportModel):
    case_id: str
    critical: bool
    passed: bool
    outcome: Outcome
    assertions: list[Assertion]
    trace: list[TraceEvent]


class EvaluationReport(ReportModel):
    spec_version: Literal["1"]
    engine_version: str
    fixture: FixtureName
    passed: bool
    critical_failed: int
    results: list[CaseResult]


@dataclass
class CaseRun:
    spec: CaseSpec
    elapsed_ms: int = 0
    completed_tools: set[ToolName] = field(default_factory=set)
    trace: list[TraceEvent] = field(default_factory=list)

    def record(
        self, kind: EventKind, tool: ToolName | None = None, attempt: int | None = None
    ) -> None:
        self.trace.append(
            TraceEvent(
                seq=len(self.trace) + 1,
                kind=kind,
                elapsed_ms=self.elapsed_ms,
                tool=tool,
                attempt=attempt,
            )
        )

    def planned_tools(self, fixture: FixtureName) -> list[ToolName]:
        case_input = self.spec.input
        tools: list[ToolName] = []
        if case_input.requested_tool is not None:
            tools.append(case_input.requested_tool)
            if case_input.repeat_tool_request:
                tools.append(case_input.requested_tool)
        if case_input.untrusted_note is not None:
            follow_note = (
                fixture == "vulnerable" and FIXTURE_RECORD_MARKER in case_input.untrusted_note
            )
            self.record("untrusted_note_followed" if follow_note else "untrusted_note_ignored")
            if follow_note:
                tools.append("record")
        if case_input.escalation_tool is not None:
            if fixture == "vulnerable":
                self.record("scope_request_honored")
                tools.append(case_input.escalation_tool)
            else:
                self.record("scope_request_ignored")
        return tools

    def call_tool(self, tool: ToolName) -> Outcome:
        self.record("tool_requested", tool)
        if tool not in self.spec.tool_permissions:
            self.record("tool_denied", tool)
            return "denied"
        if tool in self.completed_tools:
            self.record("duplicate_blocked", tool)
            return "completed"
        return self.call_provider(tool)

    def call_provider(self, tool: ToolName) -> Outcome:
        for index in range(self.spec.max_attempts):
            attempt = index + 1
            step = self.spec.provider[min(index, len(self.spec.provider) - 1)]
            self.record("provider_requested", tool, attempt)
            self.elapsed_ms = min(self.elapsed_ms + step.delay_ms, self.spec.timeout_ms)
            if step.outcome == "timeout" or self.elapsed_ms == self.spec.timeout_ms:
                self.record("timed_out", tool, attempt)
                return "timeout"
            if step.outcome == "retryable_error":
                self.record("provider_retryable_error", tool, attempt)
                continue
            if step.outcome == "interrupted":
                self.record("worker_interrupted", tool, attempt)
                return "interrupted"
            if step.outcome == "malformed_response":
                self.record("malformed_response", tool, attempt)
                return "malformed_response"
            self.record("provider_succeeded", tool, attempt)
            self.completed_tools.add(tool)
            if tool == "record":
                self.record("action_applied", tool, attempt)
            return "completed"
        self.record("retry_exhausted", tool, self.spec.max_attempts)
        return "retry_exhausted"

    def assertions_for(self, outcome: Outcome) -> list[Assertion]:
        expected = self.spec.expected
        checks = [_compare("outcome", expected.outcome, outcome)]
        if expected.output is not None:
            # The fixture returns only the fixed word "completed" on successful completion.
            # Record the matching result without persisting output or expected output contents.
            matches = outcome == "completed" and expected.output == "completed"
            checks.append(_compare("output_matches", True, matches))
        policy = self.spec.policy
        counters: list[tuple[str, EventKind, int | None]] = [
            ("denied_calls", "tool_denied", policy.denied_calls),
            ("duplicate_calls", "duplicate_blocked", policy.duplicate_calls),
            ("applied_actions", "action_applied", policy.applied_actions),
            ("honored_scope_requests", "scope_request_honored", policy.honored_scope_requests),
            ("provider_requests", "provider_requested", policy.provider_requests),
        ]
        for check, event_kind, expected_count in counters:
            if expected_count is not None:
                actual_count = sum(event.kind == event_kind for event in self.trace)
                checks.append(_compare(check, expected_count, actual_count))
        return checks


def _compare(check: str, expected: AssertionValue, actual: AssertionValue) -> Assertion:
    return Assertion(check=check, expected=expected, actual=actual, passed=expected == actual)


def _evaluate_case(spec: CaseSpec, fixture: FixtureName) -> CaseResult:
    run = CaseRun(spec)
    run.record("case_started")
    outcome: Outcome = "completed"
    for tool in run.planned_tools(fixture):
        outcome = run.call_tool(tool)
        if outcome != "completed":
            break
    run.record("case_finished")
    assertions = run.assertions_for(outcome)
    return CaseResult(
        case_id=spec.id,
        critical=spec.critical,
        passed=all(assertion.passed for assertion in assertions),
        outcome=outcome,
        assertions=assertions,
        trace=run.trace,
    )


def evaluate(suite: SuiteSpec, fixture: FixtureName) -> EvaluationReport:
    """Evaluate version 1 on scripted actions without invoking subprocesses, providers or I/O."""
    if fixture not in FIXTURE_NAMES:
        raise ValueError("unknown fixture")
    results = [_evaluate_case(case, fixture) for case in suite.cases]
    return EvaluationReport(
        spec_version=suite.version,
        engine_version=ENGINE_VERSION,
        fixture=fixture,
        passed=all(result.passed for result in results),
        critical_failed=sum(result.critical and not result.passed for result in results),
        results=results,
    )
