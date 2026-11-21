"""Tenant-scoped API for running and inspecting deterministic fixture evaluations."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from . import agent_api, reference_agent
from .api_common import Authenticated, bounded_body, bounded_json, get_settings, unique_object
from .api_common import Settings as Settings
from .db import RunDetail, RunSummary, SuiteDetail, SuiteSummary, connect, schema_current
from .engine import ENGINE_VERSION, FixtureName, evaluate
from .migrate import MigrationError
from .spec import MAX_SPEC_BYTES, SuiteSpec, parse_suite_json

LOGGER = logging.getLogger(__name__)


async def require_empty_body(request: Request, _tenant: Authenticated) -> None:
    await bounded_body(request, 0)


async def parse_suite(request: Request, _tenant: Authenticated) -> SuiteSpec:
    body = await bounded_json(request, MAX_SPEC_BYTES)
    try:
        return parse_suite_json(body)
    except ValueError:
        raise HTTPException(422, "Invalid suite specification") from None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixture: FixtureName


async def parse_run_request(request: Request, _tenant: Authenticated) -> RunRequest:
    body = await bounded_json(request, 1024)
    try:
        values = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        return RunRequest.model_validate(values)
    except (ValueError, RecursionError):
        raise HTTPException(422, "Invalid evaluation request") from None


class SuiteList(BaseModel):
    suites: list[SuiteSummary]


class RunList(BaseModel):
    runs: list[RunSummary]


def create_app(configured_settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = configured_settings or Settings.from_environment()
        yield

    app = FastAPI(
        title="AgentProof API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def prevent_caching(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "Invalid request parameters"})

    @app.exception_handler(psycopg.Error)
    async def unavailable(_request: Request, error: psycopg.Error) -> JSONResponse:
        LOGGER.warning("Database operation failed (%s)", type(error).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.exception_handler(ValidationError)
    async def invalid_stored_data(_request: Request, _error: ValidationError) -> JSONResponse:
        LOGGER.warning("Stored data failed application validation")
        return JSONResponse(status_code=503, content={"detail": "Stored data unavailable"})

    @app.get("/healthz")
    def health(request: Request) -> dict[str, str]:
        try:
            if schema_current(get_settings(request).database_url):
                return {"status": "ok"}
        except (MigrationError, psycopg.Error):
            pass
        raise HTTPException(503, "Database schema unavailable")

    @app.get("/v1/suites")
    def suites(
        tenant: Authenticated,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> SuiteList:
        with connect(tenant.database_url, tenant.owner) as database:
            return SuiteList(suites=database.list_suites(limit, offset))

    @app.post(
        "/v1/suites",
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/SuiteSpec"}}
                },
            }
        },
    )
    def create_suite(
        tenant: Authenticated, spec: Annotated[SuiteSpec, Depends(parse_suite)]
    ) -> SuiteDetail:
        """Create an immutable suite from a v1 JSON specification of at most 128 KiB."""
        with connect(tenant.database_url, tenant.owner) as database:
            return database.create_suite(spec)

    @app.get("/v1/suites/{suite_id}")
    def get_suite(tenant: Authenticated, suite_id: UUID) -> SuiteDetail:
        with connect(tenant.database_url, tenant.owner) as database:
            suite = database.get_suite(suite_id)
        if suite is None:
            raise HTTPException(404, "Suite not found")
        return suite

    @app.get("/v1/runs")
    def runs(
        tenant: Authenticated,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> RunList:
        with connect(tenant.database_url, tenant.owner) as database:
            return RunList(runs=database.list_runs(limit, offset))

    @app.get("/v1/runs/{run_id}")
    def get_run(tenant: Authenticated, run_id: UUID) -> RunDetail:
        with connect(tenant.database_url, tenant.owner) as database:
            run = database.get_run(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    @app.post(
        "/v1/suites/{suite_id}/runs",
        status_code=201,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": RunRequest.model_json_schema()}},
            }
        },
    )
    def start_run(
        tenant: Authenticated,
        suite_id: UUID,
        body: Annotated[RunRequest, Depends(parse_run_request)],
    ) -> RunDetail:
        """Run the selected fixture. Send JSON with fixture set to corrected or vulnerable."""
        with connect(tenant.database_url, tenant.owner) as database:
            suite = database.get_suite(suite_id)
            if suite is None:
                raise HTTPException(404, "Suite not found")
            if not database.try_run_slot():
                raise HTTPException(429, "An evaluation is already running", {"Retry-After": "1"})
            return database.save_run(suite, evaluate(suite.spec, body.fixture))

    @app.post(
        "/v1/runs/{run_id}/replay", status_code=201, dependencies=[Depends(require_empty_body)]
    )
    def replay(tenant: Authenticated, run_id: UUID) -> RunDetail:
        with connect(tenant.database_url, tenant.owner) as database:
            original = database.get_run(run_id)
            if original is None:
                raise HTTPException(404, "Run not found")
            suite = database.get_suite(original.suite_id)
            assert suite is not None
            if (
                original.engine_version != ENGINE_VERSION
                or original.spec_version != suite.spec.version
            ):
                raise HTTPException(409, "Original evaluation version is unavailable")
            if not database.try_run_slot():
                raise HTTPException(429, "An evaluation is already running", {"Retry-After": "1"})
            report = evaluate(suite.spec, original.fixture)
            if (
                report.results != original.results
                or report.passed != original.passed
                or report.critical_failed != original.critical_failed
            ):
                raise HTTPException(409, "Replay differs from stored evidence")
            return database.save_run(suite, report, replay_of=original.id)

    app.include_router(agent_api.router)
    app.include_router(reference_agent.router, prefix="/_reference", include_in_schema=False)
    return app


app = create_app()
