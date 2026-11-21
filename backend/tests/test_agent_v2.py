import asyncio
import hashlib
import json
from collections.abc import Callable
from importlib.resources import files
from typing import Final

import pytest

from agentproof import agent_v2
from agentproof.agent_v2 import (
    BENCHMARK_FILE,
    TICKET_ID,
    AdapterFailure,
    AgentMessage,
    AgentTurn,
    BenchmarkReport,
    EvidenceSource,
    Label,
    TokenUsage,
    ToolCall,
    load_benchmark,
    run_benchmark,
)

READ_TICKET: Final = ToolCall(
    id="fixture-read",
    name="read_ticket",
    arguments=json.dumps({"ticket_id": TICKET_ID}),
)

Respond = Callable[[tuple[AgentMessage, ...]], AgentTurn]


class FixtureAdapter:
    adapter_version = "test/1"
    model_id = "fixture-test"
    model_version = "test-revision"
    evidence: EvidenceSource = "local_reference_http"

    def __init__(self, respond: Respond) -> None:
        self._respond = respond
        self.messages: list[tuple[AgentMessage, ...]] = []
        self.timeouts: list[float] = []

    async def respond(self, messages: tuple[AgentMessage, ...], *, timeout_s: float) -> AgentTurn:
        self.messages.append(messages)
        self.timeouts.append(timeout_s)
        return self._respond(messages)


def decide_from_summary(messages: tuple[AgentMessage, ...]) -> AgentTurn:
    usage = TokenUsage(input_tokens=4, output_tokens=2)
    if messages[-1].role == "user":
        return AgentTurn(tool_calls=(READ_TICKET,), usage=usage)
    reading = messages[-1]
    assert reading.role == "tool"
    assert reading.tool_call_id == READ_TICKET.id
    assert reading.content is not None
    summary = json.loads(reading.content)["summary"].lower()
    label: Label = "general"
    if any(word in summary for word in ("invoice", "payment", "refund")):
        label = "billing"
    elif any(word in summary for word in ("error", "timeout", "connect")):
        label = "technical"
    return AgentTurn(label=label, usage=usage)


def test_ground_truth_results_metrics_and_redacted_trace() -> None:
    adapter = FixtureAdapter(decide_from_summary)
    report = asyncio.run(run_benchmark(adapter))
    raw_dataset = files("agentproof").joinpath("benchmarks", BENCHMARK_FILE).read_bytes()
    assert report.dataset_digest == hashlib.sha256(raw_dataset).hexdigest()
    assert load_benchmark().dataset_digest == report.dataset_digest
    assert report.evidence == "local_reference_http"
    assert (report.model_id, report.model_version) == ("fixture-test", "test-revision")
    assert report.passed
    assert report.critical_failed == 0
    assert report.metrics.case_count == 8
    assert report.metrics.passed_count == report.metrics.policy_passed_count == 8
    assert report.metrics.pass_rate == 1.0
    assert report.metrics.task_case_count == report.metrics.task_passed_count == 6
    assert report.metrics.estimated_cost_usd is None
    assert report.metrics.adapter_calls == len(adapter.messages) == 12
    assert report.metrics.usage_reported_calls == 12
    assert report.metrics.token_usage == TokenUsage(input_tokens=48, output_tokens=24)
    assert all(0 < deadline <= 4 for deadline in adapter.timeouts)

    by_id = {case.case_id: case for case in report.results}
    for case_id, outcome in (("malformed-response", "malformed_response"), ("timeout", "timeout")):
        case = by_id[case_id]
        assert case.fault_injection == outcome
        assert case.outcome == outcome
        assert case.adapter_calls == 0
        assert case.token_usage is None
        assert case.task_passed is None
        assert "fault_injected" in [event.kind for event in case.trace]
    recovery = by_id["retry-recovery"]
    assert recovery.outcome == "completed"
    assert recovery.adapter_calls == 2
    assert "retry_scheduled" in [event.kind for event in recovery.trace]

    result = by_id["plain-billing"]
    read = next(event for event in result.trace if event.kind == "tool_read")
    assert read.tool == "read_ticket"
    assert read.normalized_arguments is not None
    assert read.normalized_arguments.ticket_id == TICKET_ID
    for case in report.results:
        assert [event.seq for event in case.trace] == list(range(1, len(case.trace) + 1))
        ticks = [event.elapsed_ms for event in case.trace]
        assert ticks == sorted(ticks)
    serialized = report.model_dump_json()
    assert "fixture:label=general" not in serialized
    assert "The latest invoice" not in serialized
    assert BenchmarkReport.model_validate_json(serialized) == report


def test_wrong_label_fails_task_independently_of_policy() -> None:
    def wrong_label(messages: tuple[AgentMessage, ...]) -> AgentTurn:
        turn = decide_from_summary(messages)
        content = messages[-1].content
        if content is not None and "fixture:label=general" in content:
            return AgentTurn(label="general")
        return turn

    report = asyncio.run(run_benchmark(FixtureAdapter(wrong_label)))
    failed = [case for case in report.results if not case.passed]
    assert [case.case_id for case in failed] == ["untrusted-note"]
    assert report.critical_failed == 1
    assert failed[0].outcome_passed
    assert failed[0].policy_passed
    assert failed[0].task_passed is False
    assert failed[0].observed_label == "general"


def test_run_usage_aggregates_valid_large_counts() -> None:
    def counted_turn(messages: tuple[AgentMessage, ...]) -> AgentTurn:
        turn = decide_from_summary(messages)
        return AgentTurn(
            tool_calls=turn.tool_calls,
            label=turn.label,
            usage=TokenUsage(input_tokens=agent_v2.MAX_CALL_TOKENS, output_tokens=0),
        )

    report = asyncio.run(run_benchmark(FixtureAdapter(counted_turn)))
    assert report.passed
    assert report.metrics.token_usage == TokenUsage(input_tokens=120_000_000, output_tokens=0)


@pytest.mark.parametrize(
    ("name", "arguments", "blocked_kind"),
    [
        ("synthetic_unknown", '{"ticket_id":"fixture-ticket"}', "tool_denied"),
        ("read_ticket", '{"ticket_id":"synthetic_other"}', "tool_resource_denied"),
        ("read_ticket", '{"ticket_id":7}', "tool_arguments_invalid"),
        (
            "read_ticket",
            '{"ticket_id":"fixture-ticket","ticket_id":"fixture-ticket"}',
            "tool_arguments_invalid",
        ),
    ],
)
def test_blocks_invalid_tool_and_audits_batch(name: str, arguments: str, blocked_kind: str) -> None:
    def invalid_call(_: tuple[AgentMessage, ...]) -> AgentTurn:
        rejected = ToolCall(id="synthetic-call-id", name=name, arguments=arguments)
        return AgentTurn(tool_calls=(rejected, READ_TICKET))

    report = asyncio.run(run_benchmark(FixtureAdapter(invalid_call)))
    result = report.results[0]
    assert not result.passed
    assert not result.policy_passed
    assert result.outcome == "policy_violation"
    assert result.adapter_calls == 1
    assert result.tool_reads == 0
    assert [event.kind for event in result.trace].count("tool_requested") == 2
    assert any(event.kind == blocked_kind for event in result.trace)
    assert all(event.normalized_arguments is None for event in result.trace)
    rendered = report.model_dump_json()
    assert "synthetic-call-id" not in rendered
    assert "synthetic_other" not in rendered
    assert "synthetic_unknown" not in rendered


def test_blocks_duplicate_and_requires_one_read() -> None:
    def duplicate(_: tuple[AgentMessage, ...]) -> AgentTurn:
        return AgentTurn(tool_calls=(READ_TICKET,))

    blocked = asyncio.run(run_benchmark(FixtureAdapter(duplicate))).results[0]
    assert blocked.outcome == "policy_violation"
    assert blocked.tool_reads == 1
    assert any(event.kind == "tool_duplicate_blocked" for event in blocked.trace)

    task_only = asyncio.run(
        run_benchmark(FixtureAdapter(lambda _: AgentTurn(label="billing")))
    ).results[0]
    assert not task_only.passed
    assert not task_only.policy_passed
    assert task_only.task_passed is True
    assert task_only.outcome_passed


def test_adapter_fault_is_distinct_from_injected_fault() -> None:
    def unavailable(_: tuple[AgentMessage, ...]) -> AgentTurn:
        raise AdapterFailure("transient")

    report = asyncio.run(run_benchmark(FixtureAdapter(unavailable)))
    result = report.results[0]
    assert result.outcome == "retry_exhausted"
    assert result.adapter_calls == 2
    assert result.fault_injection == "none"
    kinds = [event.kind for event in result.trace]
    assert "fault_injected" not in kinds
    assert kinds.count("adapter_transient") == 2
    assert kinds.count("retry_scheduled") == 1
    assert report.metrics.token_usage is None
    assert report.metrics.usage_reported_calls == 0


def test_run_deadline_cancels_adapter_without_subsequent_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowAdapter(FixtureAdapter):
        cancelled = False

        async def respond(
            self, messages: tuple[AgentMessage, ...], *, timeout_s: float
        ) -> AgentTurn:
            self.timeouts.append(timeout_s)
            try:
                await asyncio.sleep(100)
            finally:
                self.cancelled = True
            return AgentTurn(label="billing")

    adapter = SlowAdapter(decide_from_summary)
    monkeypatch.setattr(agent_v2, "RUN_TIMEOUT_S", 0.02)
    report = asyncio.run(run_benchmark(adapter))
    assert adapter.cancelled
    assert report.metrics.adapter_calls == 1
    assert report.results[0].outcome == "timeout"
    assert {case.outcome for case in report.results[1:]} == {"run_deadline"}
    assert report.critical_failed == 8
