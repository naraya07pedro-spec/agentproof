"""Tenant-scoped persistence for immutable deterministic evaluation evidence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .engine import CaseResult, EvaluationReport, FixtureName
from .migrate import migration_checksum, migration_files
from .spec import SuiteSpec

RUN_LOCK = 716059332


class SuiteSummary(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class SuiteDetail(SuiteSummary):
    spec: SuiteSpec


class RunSummary(BaseModel):
    id: UUID
    suite_id: UUID
    suite_name: str
    fixture: FixtureName
    created_at: datetime
    replay_of: UUID | None
    evidence: Literal["deterministic_simulation"]
    passed: bool
    engine_version: str
    spec_version: str
    case_count: int
    failed_count: int
    critical_failed: int


class RunDetail(RunSummary):
    results: list[CaseResult]


class Database:
    def __init__(self, connection: psycopg.Connection[DictRow], owner: str) -> None:
        self.connection = connection
        self.owner = owner

    def list_suites(self, limit: int, offset: int) -> list[SuiteSummary]:
        rows = self.connection.execute(
            """
            SELECT id, name, created_at FROM suites WHERE owner = %s
            ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s
            """,
            (self.owner, limit, offset),
        )
        return [SuiteSummary.model_validate(row) for row in rows]

    def create_suite(self, spec: SuiteSpec) -> SuiteDetail:
        row = self.connection.execute(
            """
            INSERT INTO suites (id, owner, name, spec) VALUES (%s, %s, %s, %s)
            RETURNING id, name, created_at, spec
            """,
            (uuid4(), self.owner, spec.name, Jsonb(spec.model_dump(mode="json"))),
        ).fetchone()
        return SuiteDetail.model_validate(row)

    def get_suite(self, suite_id: UUID) -> SuiteDetail | None:
        row = self.connection.execute(
            "SELECT id, name, created_at, spec FROM suites WHERE owner = %s AND id = %s",
            (self.owner, suite_id),
        ).fetchone()
        return SuiteDetail.model_validate(row) if row is not None else None

    def list_runs(self, limit: int, offset: int) -> list[RunSummary]:
        rows = self.connection.execute(
            """
            SELECT r.*, s.name AS suite_name FROM runs r
            JOIN suites s ON (s.owner, s.id) = (r.owner, r.suite_id)
            WHERE r.owner = %s ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s
            """,
            (self.owner, limit, offset),
        )
        return [RunSummary.model_validate(row) for row in rows]

    def get_run(self, run_id: UUID) -> RunDetail | None:
        row = self.connection.execute(
            """
            SELECT r.*, s.name AS suite_name FROM runs r
            JOIN suites s ON (s.owner, s.id) = (r.owner, r.suite_id)
            WHERE r.owner = %s AND r.id = %s
            """,
            (self.owner, run_id),
        ).fetchone()
        if row is None:
            return None
        rows = self.connection.execute(
            "SELECT result FROM run_results WHERE run_id = %s ORDER BY position", (run_id,)
        )
        row["results"] = [result["result"] for result in rows]
        return RunDetail.model_validate(row)

    def try_run_slot(self) -> bool:
        row = self.connection.execute(
            "SELECT pg_try_advisory_xact_lock(%s) AS acquired", (RUN_LOCK,)
        ).fetchone()
        return row is not None and row["acquired"] is True

    def save_run(
        self, suite: SuiteDetail, report: EvaluationReport, replay_of: UUID | None = None
    ) -> RunDetail:
        run_id = uuid4()
        row = self.connection.execute(
            """
            INSERT INTO runs (
                id, owner, suite_id, replay_of, fixture, engine_version, spec_version,
                evidence, passed, case_count, failed_count, critical_failed
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'deterministic_simulation', %s, %s, %s, %s)
            RETURNING *
            """,
            (
                run_id,
                self.owner,
                suite.id,
                replay_of,
                report.fixture,
                report.engine_version,
                report.spec_version,
                report.passed,
                len(report.results),
                sum(not result.passed for result in report.results),
                report.critical_failed,
            ),
        ).fetchone()
        assert row is not None
        with self.connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO run_results (run_id, position, result) VALUES (%s, %s, %s)",
                [
                    (run_id, index, Jsonb(result.model_dump(mode="json")))
                    for index, result in enumerate(report.results)
                ],
            )
        return RunDetail.model_validate(
            {**row, "suite_name": suite.name, "results": report.results}
        )


@contextmanager
def connect(database_url: str, owner: str) -> Iterator[Database]:
    with psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '2s'")
        yield Database(connection, owner)


def schema_current(database_url: str) -> bool:
    with connect(database_url, "") as database:
        rows = database.connection.execute("SELECT name, checksum FROM schema_migrations")
        applied = {row["name"]: row["checksum"] for row in rows}
    expected = {name: migration_checksum(source) for name, source in migration_files().items()}
    return applied == expected
