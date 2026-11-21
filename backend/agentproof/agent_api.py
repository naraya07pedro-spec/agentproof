"""Workspace API for the bundled reference-agent benchmark."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .agent_db import (
    AgentRunDetail,
    AgentRunSummary,
    get_agent_run,
    list_agent_runs,
    save_agent_run,
)
from .agent_v2 import AgentAdapter, EvidenceSource, load_benchmark, run_benchmark
from .api_common import Authenticated, bounded_json, get_settings, unique_object
from .db import connect
from .model_adapter import (
    REFERENCE_AGENT_VERSIONS,
    REFERENCE_MODEL,
    ModelConfigurationError,
    ReferenceVariant,
    external_adapter_from_environment,
    reference_adapter,
)

router = APIRouter(prefix="/v2")
ResourceId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
EXTERNAL_AGENT_ID = "external-model"


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str
    model: str = REFERENCE_MODEL
    agent_version: str | None
    evidence_mode: EvidenceSource = "local_reference_http"


class AgentList(BaseModel):
    agents: list[AgentSummary]


class SuiteSummary(BaseModel):
    id: str
    name: str
    description: str
    version: str
    dataset_digest: str


class SuiteList(BaseModel):
    suites: list[SuiteSummary]


class RunList(BaseModel):
    runs: list[AgentRunSummary]


class StartAgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: ResourceId
    suite_id: ResourceId


REFERENCE_AGENTS: dict[str, tuple[ReferenceVariant, AgentSummary]] = {
    "reference-corrected": (
        "corrected",
        AgentSummary(
            id="reference-corrected",
            name="Corrected reference agent",
            description="Classifies synthetic support tickets and respects the simulated tool policy.",
            agent_version=REFERENCE_AGENT_VERSIONS["corrected"],
        ),
    ),
    "reference-vulnerable": (
        "vulnerable",
        AgentSummary(
            id="reference-vulnerable",
            name="Vulnerable reference agent",
            description="Intentionally violates a synthetic instruction-boundary test while using fake tools.",
            agent_version=REFERENCE_AGENT_VERSIONS["vulnerable"],
        ),
    ),
}


def suite_metadata() -> SuiteSummary:
    benchmark = load_benchmark()
    return SuiteSummary(
        id=benchmark.id,
        name=benchmark.name,
        description=benchmark.description,
        version=benchmark.version,
        dataset_digest=benchmark.dataset_digest,
    )


async def parse_start(request: Request, _tenant: Authenticated) -> StartAgentRun:
    body = await bounded_json(request, 1024)
    try:
        values = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
        return StartAgentRun.model_validate(values)
    except (ValueError, RecursionError):
        raise HTTPException(422, "Invalid benchmark request") from None


def external_profile() -> tuple[AgentSummary, AgentAdapter]:
    try:
        adapter = external_adapter_from_environment()
    except ModelConfigurationError:
        raise HTTPException(503, "External model configuration unavailable") from None
    return (
        AgentSummary(
            id=EXTERNAL_AGENT_ID,
            name="Configured external model",
            description="Runs the bundled synthetic benchmark against the operator-configured provider.",
            model=adapter.model_id,
            agent_version=adapter.model_version,
            evidence_mode="external_model_http",
        ),
        adapter,
    )


@router.get("/agents")
def agents(request: Request, tenant: Authenticated) -> AgentList:
    catalog = [summary for _, summary in REFERENCE_AGENTS.values()]
    if get_settings(request).model_workspace == tenant.owner:
        external, _ = external_profile()
        catalog.append(external)
    return AgentList(agents=catalog)


@router.get("/suites")
def suites(_tenant: Authenticated) -> SuiteList:
    return SuiteList(suites=[suite_metadata()])


@router.get("/runs")
def runs(
    tenant: Authenticated,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> RunList:
    with connect(tenant.database_url, tenant.owner) as database:
        return RunList(runs=list_agent_runs(database, limit, offset))


@router.get("/runs/{run_id}")
def get_run(tenant: Authenticated, run_id: UUID) -> AgentRunDetail:
    with connect(tenant.database_url, tenant.owner) as database:
        run = get_agent_run(database, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.post(
    "/runs",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": StartAgentRun.model_json_schema()}},
        },
    },
)
def start_run(
    request: Request,
    tenant: Authenticated,
    body: Annotated[StartAgentRun, Depends(parse_start)],
) -> AgentRunDetail:
    settings = get_settings(request)
    entry = REFERENCE_AGENTS.get(body.agent_id)
    external = body.agent_id == EXTERNAL_AGENT_ID and settings.model_workspace == tenant.owner
    if entry is None and not external:
        raise HTTPException(404, "Agent not found")
    suite = suite_metadata()
    if body.suite_id != suite.id:
        raise HTTPException(404, "Suite not found")
    with connect(tenant.database_url, tenant.owner) as database:
        if not database.try_run_slot():
            raise HTTPException(429, "An evaluation is already running", {"Retry-After": "1"})
        run_id = uuid4()
        if entry is None:
            agent, adapter = external_profile()
        else:
            variant, agent = entry
            adapter = reference_adapter(variant, port=settings.reference_port)
        report = asyncio.run(run_benchmark(adapter))
        if (
            report.evidence != agent.evidence_mode
            or report.model_id != agent.model
            or report.model_version != agent.agent_version
            or report.benchmark_id != suite.id
            or report.benchmark_version != suite.version
            or report.dataset_digest != suite.dataset_digest
        ):
            raise HTTPException(503, "Benchmark metadata unavailable")
        return save_agent_run(
            database,
            run_id,
            agent_id=agent.id,
            agent_name=agent.name,
            agent_version=agent.agent_version,
            suite_name=suite.name,
            report=report,
        )
