"""PostgreSQL storage for authorized benchmark runs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .agent_v2 import BenchmarkMetrics, BenchmarkReport, BenchmarkResult, EvidenceSource
from .db import Database


class AgentRunSummary(BaseModel):
    id: UUID
    correlation_id: UUID
    agent_id: str
    agent_name: str
    agent_version: str | None
    model: str
    suite_id: str
    suite_name: str
    suite_version: str
    dataset_digest: str
    engine_version: str
    adapter_version: str
    evidence: EvidenceSource
    passed: bool
    case_count: int
    failed_count: int
    critical_failed: int
    cost_usd: None = None
    created_at: datetime


class AgentRunDetail(AgentRunSummary):
    metrics: BenchmarkMetrics
    results: list[BenchmarkResult]


def list_agent_runs(database: Database, limit: int, offset: int) -> list[AgentRunSummary]:
    rows = database.connection.execute(
        """
        SELECT *, id AS correlation_id FROM agent_runs WHERE owner = %s
        ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s
        """,
        (database.owner, limit, offset),
    )
    return [AgentRunSummary.model_validate(row) for row in rows]


def get_agent_run(database: Database, run_id: UUID) -> AgentRunDetail | None:
    row = database.connection.execute(
        "SELECT *, id AS correlation_id FROM agent_runs WHERE owner = %s AND id = %s",
        (database.owner, run_id),
    ).fetchone()
    if row is None:
        return None
    rows = database.connection.execute(
        "SELECT result FROM agent_run_results WHERE run_id = %s ORDER BY position", (run_id,)
    )
    row["results"] = [result["result"] for result in rows]
    return AgentRunDetail.model_validate(row)


def save_agent_run(
    database: Database,
    run_id: UUID,
    *,
    agent_id: str,
    agent_name: str,
    agent_version: str | None,
    suite_name: str,
    report: BenchmarkReport,
) -> AgentRunDetail:
    row = database.connection.execute(
        """
        INSERT INTO agent_runs (
            id, owner, agent_id, agent_name, agent_version, model, suite_id, suite_name,
            suite_version, dataset_digest, engine_version, adapter_version, evidence,
            passed, case_count, failed_count, critical_failed, metrics
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *, id AS correlation_id
        """,
        (
            run_id,
            database.owner,
            agent_id,
            agent_name,
            agent_version,
            report.model_id,
            report.benchmark_id,
            suite_name,
            report.benchmark_version,
            report.dataset_digest,
            report.engine_version,
            report.adapter_version,
            report.evidence,
            report.passed,
            len(report.results),
            sum(not result.passed for result in report.results),
            report.critical_failed,
            Jsonb(report.metrics.model_dump(mode="json")),
        ),
    ).fetchone()
    assert row is not None
    with database.connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO agent_run_results (run_id, position, result) VALUES (%s, %s, %s)",
            [
                (run_id, index, Jsonb(result.model_dump(mode="json")))
                for index, result in enumerate(report.results)
            ],
        )
    return AgentRunDetail.model_validate({**row, "results": report.results})
