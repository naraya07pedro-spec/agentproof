"""Fail-closed policy inspection of bounded, externally supplied agent event traces.

Imported evidence has UNVERIFIED origin. A passing inspection is not a release
attestation, and does not establish that a real agent generated the events.
"""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TRACE_BYTES = 262_144
MAX_POLICY_BYTES = 65_536
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._:-]{0,63}$")]
EventKind = Literal[
    "tool_requested",
    "tool_denied",
    "approval_granted",
    "action_applied",
    "run_completed",
]
FindingCode = Literal[
    "sequence_gap",
    "duplicate_request",
    "unauthorized_tool_attempt",
    "unauthorized_scope_attempt",
    "orphan_denial",
    "orphan_approval",
    "unmatched_effect",
    "unapproved_effect",
    "duplicate_effect",
    "request_budget_exceeded",
    "effect_budget_exceeded",
    "missing_completion",
    "invalid_completion",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class RecordedEvent(StrictModel):
    seq: int = Field(ge=1, le=512)
    kind: EventKind
    call_id: Identifier | None = None
    tool: Identifier | None = None
    scope: Identifier | None = None

    @model_validator(mode="after")
    def event_fields(self) -> Self:
        values = (self.call_id, self.tool, self.scope)
        if self.kind == "run_completed":
            if any(value is not None for value in values):
                raise ValueError("completion event cannot contain call metadata")
        elif any(value is None for value in values):
            raise ValueError("tool events require call_id, tool and scope")
        return self


class TracePolicy(StrictModel):
    allowed_tools: list[Identifier] = Field(max_length=32)
    allowed_scopes: list[Identifier] = Field(max_length=32)
    approval_required_tools: list[Identifier] = Field(default_factory=list, max_length=32)
    max_tool_requests: int = Field(default=64, ge=0, le=512)
    max_applied_actions: int = Field(default=16, ge=0, le=512)

    @model_validator(mode="after")
    def valid_policy(self) -> Self:
        for values in (self.allowed_tools, self.allowed_scopes, self.approval_required_tools):
            if len(values) != len(set(values)):
                raise ValueError("policy lists must contain unique entries")
        if not set(self.approval_required_tools).issubset(self.allowed_tools):
            raise ValueError("approval tools must be allowed tools")
        return self


class RecordedTrace(StrictModel):
    version: Literal["1"]
    run_id: Identifier
    events: list[RecordedEvent] = Field(min_length=1, max_length=512)


class Finding(StrictModel):
    code: FindingCode
    seq: int | None


class TraceInspection(StrictModel):
    format_version: Literal["1"] = "1"
    evidence_origin: Literal["imported_unverified"] = "imported_unverified"
    release_eligible: Literal[False] = False
    policy_passed: bool
    run_digest_sha256: str
    findings: list[Finding]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite numbers are unsupported")


def load_trace(path: Path) -> RecordedTrace:
    with path.open("rb") as stream:
        raw = stream.read(MAX_TRACE_BYTES + 1)
    if len(raw) > MAX_TRACE_BYTES:
        raise ValueError("trace exceeds size limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return RecordedTrace.model_validate(value)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError("invalid trace") from None


def load_policy(path: Path) -> TracePolicy:
    """Load operator-controlled policy separately from untrusted trace data."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_POLICY_BYTES + 1)
    if len(raw) > MAX_POLICY_BYTES:
        raise ValueError("policy exceeds size limit")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return TracePolicy.model_validate(value)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ValueError("invalid policy") from None


def inspect_trace(trace: RecordedTrace, policy: TracePolicy) -> TraceInspection:
    """Check event ordering and modeled policy assertions, not event authenticity."""
    findings: list[Finding] = []
    requests: dict[str, tuple[str, str]] = {}
    approvals: set[str] = set()
    effects: set[str] = set()
    request_count = 0
    effect_count = 0
    completed = False

    def fail(code: FindingCode, seq: int | None = None) -> None:
        findings.append(Finding(code=code, seq=seq))

    for index, event in enumerate(trace.events, start=1):
        if event.seq != index:
            fail("sequence_gap", event.seq)
        if event.kind == "run_completed":
            if completed or index != len(trace.events):
                fail("invalid_completion", event.seq)
            completed = True
            continue
        if completed:
            fail("invalid_completion", event.seq)
        if event.call_id is None or event.tool is None or event.scope is None:
            continue  # blocked by model validation
        call_id = event.call_id
        request = requests.get(call_id)
        expected = (event.tool, event.scope)

        if event.kind == "tool_requested":
            request_count += 1
            if request is not None:
                fail("duplicate_request", event.seq)
            else:
                requests[call_id] = expected
            if event.tool not in policy.allowed_tools:
                fail("unauthorized_tool_attempt", event.seq)
            if event.scope not in policy.allowed_scopes:
                fail("unauthorized_scope_attempt", event.seq)
        elif event.kind == "tool_denied":
            if request != expected:
                fail("orphan_denial", event.seq)
        elif event.kind == "approval_granted":
            if request != expected or call_id in approvals:
                fail("orphan_approval", event.seq)
            else:
                approvals.add(call_id)
        elif event.kind == "action_applied":
            effect_count += 1
            if request != expected:
                fail("unmatched_effect", event.seq)
            if call_id in effects:
                fail("duplicate_effect", event.seq)
            effects.add(call_id)
            if event.tool in policy.approval_required_tools and call_id not in approvals:
                fail("unapproved_effect", event.seq)

    if not completed:
        fail("missing_completion")
    if request_count > policy.max_tool_requests:
        fail("request_budget_exceeded")
    if effect_count > policy.max_applied_actions:
        fail("effect_budget_exceeded")

    canonical = json.dumps(
        {"trace": trace.model_dump(mode="json"), "policy": policy.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    ).encode("utf-8")
    return TraceInspection(
        policy_passed=not findings,
        run_digest_sha256=hashlib.sha256(canonical).hexdigest(),
        findings=findings,
    )
