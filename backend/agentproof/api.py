"""Tenant-scoped API for running and inspecting deterministic fixture evaluations."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Self, cast
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.requests import ClientDisconnect

from .db import RunDetail, RunSummary, SuiteDetail, SuiteSummary, connect, schema_current
from .engine import ENGINE_VERSION, FixtureName, evaluate
from .migrate import MigrationError
from .spec import MAX_SPEC_BYTES, SuiteSpec, parse_suite_json

LOGGER = logging.getLogger(__name__)
WORKSPACE_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
TOKEN = re.compile(r"[A-Za-z0-9_-]{24,128}")
BEARER = HTTPBearer(auto_error=False)
BODY_READ_SECONDS = 5


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    token_hashes: dict[str, bytes] = field(repr=False)

    @classmethod
    def from_environment(cls) -> Self:
        database_url = os.environ.get("AGENTPROOF_DATABASE_URL", "")
        if not database_url:
            raise RuntimeError("AGENTPROOF_DATABASE_URL is required")
        raw_tokens = os.environ.get("AGENTPROOF_API_TOKENS", "")
        try:
            values: object = json.loads(raw_tokens, object_pairs_hook=unique_object)
            if not isinstance(values, dict) or not 1 <= len(values) <= 64:
                raise ValueError
            hashes: dict[str, bytes] = {}
            for owner, token in values.items():
                if (
                    not WORKSPACE_ID.fullmatch(owner)
                    or not isinstance(token, str)
                    or not TOKEN.fullmatch(token)
                ):
                    raise ValueError
                hashes[owner] = hashlib.sha256(token.encode("ascii")).digest()
            if len(set(hashes.values())) != len(hashes):
                raise ValueError
        except (ValueError, RecursionError):
            raise RuntimeError(
                "AGENTPROOF_API_TOKENS must map workspace IDs to unique tokens"
            ) from None
        return cls(database_url, hashes)


@dataclass(frozen=True)
class Tenant:
    owner: str
    database_url: str = field(repr=False)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(BEARER)],
) -> Tenant:
    if (
        credentials is not None
        and len(request.headers.getlist("authorization")) == 1
        and TOKEN.fullmatch(credentials.credentials)
    ):
        settings = get_settings(request)
        supplied = hashlib.sha256(credentials.credentials.encode("ascii")).digest()
        owner = None
        for workspace, digest in settings.token_hashes.items():
            if hmac.compare_digest(supplied, digest):
                owner = workspace
        if owner is not None:
            return Tenant(owner, settings.database_url)
    raise HTTPException(401, "Bearer authentication required", {"WWW-Authenticate": "Bearer"})


Authenticated = Annotated[Tenant, Depends(authenticate)]


async def bounded_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None:
        if not re.fullmatch(r"[0-9]{1,20}", length):
            raise HTTPException(400, "Invalid Content-Length")
        if int(length) > limit:
            raise HTTPException(413, "Request body exceeds size limit")
    body = bytearray()
    try:
        async with asyncio.timeout(BODY_READ_SECONDS):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise HTTPException(413, "Request body exceeds size limit")
                body.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Request body read timed out") from None
    except ClientDisconnect:
        raise HTTPException(400, "Incomplete request") from None
    return bytes(body)


async def bounded_json(request: Request, limit: int) -> bytes:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(415, "Expected application/json")
    return await bounded_body(request, limit)


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

    return app


app = create_app()
