from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agentproof import agent_api, api_common
from agentproof import migrate as migrate_module
from agentproof.agent_v2 import AgentMessage, AgentTurn
from agentproof.api import Settings, create_app
from agentproof.db import RUN_LOCK
from agentproof.migrate import MigrationError, migrate

EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "examples" / "defensive-suite.json"
TOKEN_A = "demo_test_token_for_workspace_a"
TOKEN_B = "demo_test_token_for_workspace_b"
OWNER_A = {"Authorization": f"Bearer {TOKEN_A}"}
OWNER_B = {"Authorization": f"Bearer {TOKEN_B}"}
TICKET_SUITE = "ticket-triage"


def settings(database_url: str) -> Settings:
    return Settings(
        database_url,
        {
            "workspace_a": hashlib.sha256(TOKEN_A.encode("ascii")).digest(),
            "workspace_b": hashlib.sha256(TOKEN_B.encode("ascii")).digest(),
        },
    )


@pytest.fixture
def no_database() -> Iterator[TestClient]:
    with TestClient(create_app(settings("host=127.0.0.1 port=1"))) as client:
        yield client


@pytest.fixture
def database_url() -> Iterator[str]:
    url = os.environ.get("AGENTPROOF_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENTPROOF_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    schema = f"test_{uuid4().hex}"
    with psycopg.connect(url) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(url, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def api(database_url: str) -> Iterator[TestClient]:
    migrate(database_url)
    with TestClient(create_app(settings(database_url))) as client:
        yield client


@pytest.fixture
def reference_client(database_url: str) -> Iterator[httpx2.Client]:
    migrate(database_url)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port: int = listener.getsockname()[1]
        app = create_app(replace(settings(database_url), reference_port=port))
        server = uvicorn.Server(
            uvicorn.Config(app, access_log=False, ws="none", log_level="warning")
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert server.started, "Reference API server did not start"
            with httpx2.Client(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=45
            ) as client:
                yield client
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "Reference API server did not stop"


def create_suite(client: TestClient) -> str:
    response = client.post(
        "/v1/suites",
        headers={**OWNER_A, "Content-Type": "application/json"},
        content=EXAMPLE_PATH.read_bytes(),
    )
    assert response.status_code == 201, response.text
    suite_id: str = response.json()["id"]
    return suite_id


def test_authentication_precedes_parsing_and_database_access(no_database: TestClient) -> None:
    response = no_database.post("/v1/suites", content=b"malformed")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"
    assert (
        no_database.get("/v1/runs", headers={"Authorization": "Bearer invalid"}).status_code == 401
    )
    duplicate = [
        ("Authorization", OWNER_A["Authorization"]),
        ("Authorization", OWNER_B["Authorization"]),
    ]
    assert no_database.get("/v1/runs", headers=duplicate).status_code == 401
    assert no_database.get("/healthz").status_code == 503


def test_openapi_schema_is_served_without_browser_documentation(no_database: TestClient) -> None:
    response = no_database.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    schema = response.json()
    assert "/v1/suites" in schema["paths"]
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert no_database.get("/docs").status_code == 404
    assert no_database.get("/docs/oauth2-redirect").status_code == 404
    assert no_database.get("/redoc").status_code == 404


def test_settings_reject_misconfigured_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTPROOF_DATABASE_URL", "host=localhost")
    monkeypatch.setenv("AGENTPROOF_API_TOKENS", json.dumps({"a": TOKEN_A, "b": TOKEN_A}))
    with pytest.raises(RuntimeError, match="must map workspace IDs") as error:
        Settings.from_environment()
    assert TOKEN_A not in str(error.value)
    monkeypatch.setenv("AGENTPROOF_API_TOKENS", json.dumps({"a": TOKEN_A, "b": TOKEN_B}))
    assert set(Settings.from_environment().token_hashes) == {"a", "b"}


def test_size_and_parse_failures_do_not_echo_input(no_database: TestClient) -> None:
    headers = {**OWNER_A, "Content-Type": "application/json"}
    response = no_database.post(
        "/v1/suites", content=b'{"version":"private-content","version":"1"}', headers=headers
    )
    assert response.status_code == 422
    assert "private-content" not in response.text
    response = no_database.post(f"/v1/runs/{uuid4()}/replay", json={}, headers=OWNER_A)
    assert response.status_code == 413
    response = no_database.post("/v1/suites", content=b" " * 131_073, headers=headers)
    assert response.status_code == 413
    response = no_database.post("/v1/suites", content=iter([b" " * 65_536] * 3), headers=headers)
    assert response.status_code == 413
    response = no_database.post("/v1/suites", content=b"{}", headers=OWNER_A)
    assert response.status_code == 415
    response = no_database.post(
        f"/v1/suites/{uuid4()}/runs", headers=OWNER_A, json={"fixture": "unknown"}
    )
    assert response.status_code == 422
    response = no_database.get("/v1/runs?limit=private-content", headers=OWNER_A)
    assert response.status_code == 422
    assert "private-content" not in response.text


def test_body_read_has_a_wall_clock_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_common, "BODY_READ_SECONDS", 0.01)
    app = create_app(settings("host=127.0.0.1 port=1"))

    async def slowly() -> AsyncIterator[bytes]:
        yield b"{"
        await asyncio.sleep(1)
        yield b"}"

    async def request() -> int:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/suites",
                headers={**OWNER_A, "Content-Type": "application/json"},
                content=slowly(),
            )
            return response.status_code

    with TestClient(app):
        assert asyncio.run(request()) == 408


def test_fresh_migration_repeat_and_checksum_drift(database_url: str) -> None:
    assert migrate(database_url) == ["001_initial.sql", "002_agent_runs.sql"]
    assert migrate(database_url) == []
    with TestClient(create_app(settings(database_url))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        with psycopg.connect(database_url) as connection:
            connection.execute("UPDATE schema_migrations SET checksum = 'tampered'")
        assert client.get("/healthz").status_code == 503
    with pytest.raises(MigrationError, match="checksum mismatch"):
        migrate(database_url)


def test_v2_migration_preserves_v1_run_evidence(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_name = "001_initial.sql"
    first_migration = migrate_module.migration_files()[first_name]
    with monkeypatch.context() as v1:
        v1.setattr(migrate_module, "migration_files", lambda: {first_name: first_migration})
        assert migrate(database_url) == [first_name]
    with TestClient(create_app(settings(database_url))) as client:
        suite_id = create_suite(client)
        created = client.post(
            f"/v1/suites/{suite_id}/runs", json={"fixture": "corrected"}, headers=OWNER_A
        )
        assert created.status_code == 201, created.text
        run = created.json()
        assert migrate(database_url) == ["002_agent_runs.sql"]
        assert migrate(database_url) == []
        assert client.get("/healthz").status_code == 200
        assert client.get(f"/v1/runs/{run['id']}", headers=OWNER_A).json() == run
        assert client.get("/v2/runs", headers=OWNER_A).json() == {"runs": []}


def test_run_is_persisted_with_sanitized_evidence_and_replays_exactly(api: TestClient) -> None:
    suite_id = create_suite(api)
    original = api.post(
        f"/v1/suites/{suite_id}/runs", json={"fixture": "corrected"}, headers=OWNER_A
    )
    assert original.status_code == 201, original.text
    run = original.json()
    assert run["passed"] is True
    assert run["evidence"] == "deterministic_simulation"
    assert run["case_count"] == len(run["results"]) > 0
    assert "[fixture:perform-record]" not in original.text
    assert api.get(f"/v1/runs/{run['id']}", headers=OWNER_A).json() == run
    replay = api.post(f"/v1/runs/{run['id']}/replay", headers=OWNER_A)
    assert replay.status_code == 201, replay.text
    assert replay.json()["replay_of"] == run["id"]
    assert replay.json()["results"] == run["results"]
    regression = api.post(
        f"/v1/suites/{suite_id}/runs", json={"fixture": "vulnerable"}, headers=OWNER_A
    )
    assert regression.status_code == 201
    assert regression.json()["passed"] is False
    assert regression.json()["critical_failed"] > 0
    listed = api.get("/v1/runs?limit=2&offset=1", headers=OWNER_A).json()["runs"]
    assert [summary["id"] for summary in listed] == [replay.json()["id"], run["id"]]
    assert all("results" not in summary for summary in listed)


def test_all_reads_and_mutations_enforce_workspace_ownership(api: TestClient) -> None:
    suite_id = create_suite(api)
    started = api.post(
        f"/v1/suites/{suite_id}/runs", json={"fixture": "corrected"}, headers=OWNER_A
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    assert api.get("/v1/suites", headers=OWNER_B).json() == {"suites": []}
    assert api.get("/v1/runs", headers=OWNER_B).json() == {"runs": []}
    assert api.get(f"/v1/suites/{suite_id}", headers=OWNER_B).status_code == 404
    assert api.get(f"/v1/runs/{run_id}", headers=OWNER_B).status_code == 404
    assert api.post(f"/v1/runs/{run_id}/replay", headers=OWNER_B).status_code == 404
    assert (
        api.post(
            f"/v1/suites/{suite_id}/runs", headers=OWNER_B, json={"fixture": "corrected"}
        ).status_code
        == 404
    )
    assert len(api.get("/v1/runs", headers=OWNER_A).json()["runs"]) == 1


def test_replay_refuses_unsupported_or_different_evidence(
    api: TestClient, database_url: str
) -> None:
    suite_id = create_suite(api)
    started = api.post(
        f"/v1/suites/{suite_id}/runs", json={"fixture": "corrected"}, headers=OWNER_A
    )
    assert started.status_code == 201, started.text
    run = started.json()
    with psycopg.connect(database_url) as connection:
        connection.execute("UPDATE runs SET engine_version = '0.0.0'")
    replay_url = f"/v1/runs/{run['id']}/replay"
    unavailable = api.post(replay_url, headers=OWNER_A)
    assert unavailable.status_code == 409
    assert unavailable.json() == {"detail": "Original evaluation version is unavailable"}
    with psycopg.connect(database_url) as connection:
        connection.execute("UPDATE runs SET engine_version = %s", (run["engine_version"],))
        connection.execute("""
            UPDATE run_results SET result = jsonb_set(result, '{case_id}', '"different"')
            WHERE position = 0
            """)
    different = api.post(replay_url, headers=OWNER_A)
    assert different.status_code == 409
    assert different.json() == {"detail": "Replay differs from stored evidence"}
    assert len(api.get("/v1/runs", headers=OWNER_A).json()["runs"]) == 1


def test_database_admission_rejects_concurrent_evaluation(
    api: TestClient, database_url: str
) -> None:
    suite_id = create_suite(api)
    url = f"/v1/suites/{suite_id}/runs"
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (RUN_LOCK,))
        busy = api.post(url, json={"fixture": "corrected"}, headers=OWNER_A)
        assert busy.status_code == 429
        assert busy.headers["Retry-After"] == "1"
        assert api.get("/v1/runs", headers=OWNER_A).json() == {"runs": []}
    assert api.post(url, json={"fixture": "corrected"}, headers=OWNER_A).status_code == 201


def test_failed_result_write_rolls_back_entire_run(api: TestClient, database_url: str) -> None:
    suite_id = create_suite(api)
    url = f"/v1/suites/{suite_id}/runs"
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "ALTER TABLE run_results ADD CONSTRAINT forced_failure CHECK (position < 1)"
        )
    failed = api.post(url, json={"fixture": "corrected"}, headers=OWNER_A)
    assert failed.status_code == 503
    assert failed.json() == {"detail": "Database unavailable"}
    assert api.get("/v1/runs", headers=OWNER_A).json() == {"runs": []}
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM run_results").fetchone() == (0,)
        connection.execute("ALTER TABLE run_results DROP CONSTRAINT forced_failure")
    assert api.post(url, json={"fixture": "corrected"}, headers=OWNER_A).status_code == 201


def test_v2_opt_in_requires_matching_configured_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTPROOF_DATABASE_URL", "host=localhost")
    monkeypatch.setenv("AGENTPROOF_API_TOKENS", json.dumps({"workspace_a": TOKEN_A}))
    monkeypatch.setenv("AGENTPROOF_MODEL_WORKSPACE", "workspace_a")
    monkeypatch.delenv("AGENTPROOF_MODEL_ENABLE_EXTERNAL", raising=False)
    assert Settings.from_environment().model_workspace is None
    monkeypatch.setenv("AGENTPROOF_MODEL_ENABLE_EXTERNAL", "1")
    assert Settings.from_environment().model_workspace == "workspace_a"
    monkeypatch.setenv("AGENTPROOF_MODEL_WORKSPACE", "absent")
    with pytest.raises(RuntimeError, match="must identify a configured workspace"):
        Settings.from_environment()


def test_v2_run_rejects_request_controlled_provider_configuration(no_database: TestClient) -> None:
    payload = {"agent_id": "reference-corrected", "suite_id": TICKET_SUITE}
    assert no_database.get("/v2/agents").status_code == 401
    assert no_database.get("/v2/suites").status_code == 401
    assert no_database.get("/v2/runs").status_code == 401
    assert no_database.post("/v2/runs", json=payload).status_code == 401
    for field in ("url", "token", "prompt", "model"):
        invalid = no_database.post(
            "/v2/runs", json={**payload, field: "private-value"}, headers=OWNER_A
        )
        assert invalid.status_code == 422
        assert "private-value" not in invalid.text
    disabled = no_database.post(
        "/v2/runs", json={**payload, "agent_id": "external-model"}, headers=OWNER_A
    )
    assert disabled.status_code == 404
    unknown = no_database.post("/v2/runs", json={**payload, "suite_id": "unknown"}, headers=OWNER_A)
    assert unknown.status_code == 404


def test_v2_opt_in_profile_is_visible_only_to_authorized_workspace(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(database_url)
    construction_count = 0

    async def mock_respond(_messages: tuple[AgentMessage, ...], *, timeout_s: float) -> AgentTurn:
        assert timeout_s > 0
        return AgentTurn(label="general")

    def mock_factory() -> object:
        nonlocal construction_count
        construction_count += 1
        return SimpleNamespace(
            model_id="mock-model",
            model_version=None,
            adapter_version="mock-adapter/1",
            evidence="external_model_http",
            credential="fake-secret-for-test",
            respond=mock_respond,
        )

    monkeypatch.setattr(agent_api, "external_adapter_from_environment", mock_factory)
    config = replace(settings(database_url), model_workspace="workspace_a")
    with TestClient(create_app(config)) as client:
        a_catalog = client.get("/v2/agents", headers=OWNER_A)
        assert a_catalog.status_code == 200
        external = a_catalog.json()["agents"][-1]
        assert external["id"] == "external-model"
        assert external["model"] == "mock-model"
        assert external["agent_version"] is None
        assert external["evidence_mode"] == "external_model_http"
        assert "fake-secret-for-test" not in a_catalog.text
        b_catalog = client.get("/v2/agents", headers=OWNER_B)
        assert b_catalog.status_code == 200
        assert all(agent["id"] != "external-model" for agent in b_catalog.json()["agents"])
        payload = {"agent_id": "external-model", "suite_id": TICKET_SUITE}
        assert client.post("/v2/runs", json=payload, headers=OWNER_B).status_code == 404
        with psycopg.connect(database_url) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (RUN_LOCK,))
            busy = client.post("/v2/runs", json=payload, headers=OWNER_A)
            assert busy.status_code == 429
            assert busy.headers["Retry-After"] == "1"
        assert construction_count == 1
        created = client.post("/v2/runs", json=payload, headers=OWNER_A)
        assert created.status_code == 201
        assert construction_count == 2
        run = created.json()
        assert run["agent_id"] == "external-model"
        assert run["evidence"] == "external_model_http"
        assert "fake-secret-for-test" not in created.text
        assert client.get(f"/v2/runs/{run['id']}", headers=OWNER_B).status_code == 404


def test_v2_evaluates_both_http_agents_and_persists_scoped_evidence(
    reference_client: httpx2.Client,
) -> None:
    catalog = reference_client.get("/v2/agents", headers=OWNER_A).json()["agents"]
    assert [entry["id"] for entry in catalog] == ["reference-corrected", "reference-vulnerable"]
    assert catalog[0]["agent_version"] != catalog[1]["agent_version"]
    suite = reference_client.get("/v2/suites", headers=OWNER_A).json()["suites"][0]
    assert suite["id"] == TICKET_SUITE
    assert suite["version"] == "2"
    for entry, expected_verdict in zip(catalog, [True, False], strict=True):
        created = reference_client.post(
            "/v2/runs",
            json={"agent_id": entry["id"], "suite_id": suite["id"]},
            headers=OWNER_A,
        )
        assert created.status_code == 201, created.text
        run = created.json()
        assert run["passed"] is expected_verdict, run
        assert run["agent_version"] == entry["agent_version"]
        assert run["evidence"] == "local_reference_http"
        assert run["dataset_digest"] == suite["dataset_digest"]
        assert run["model"] == entry["model"]
        assert run["cost_usd"] is None
        assert run["correlation_id"] == run["id"]
        assert run["metrics"]["adapter_calls"] > 0
        assert run["case_count"] == len(run["results"]) == 8
        timeout = next(result for result in run["results"] if result["case_id"] == "timeout")
        assert timeout["outcome"] == "timeout"
        assert timeout["fault_injection"] == "timeout"
        if not expected_verdict:
            denied = next(result for result in run["results"] if result["case_id"] == "denied-tool")
            assert denied["policy_passed"] is False
            assert any(event["kind"] == "tool_denied" for event in denied["trace"])
        assert "fixture:label=general" not in created.text
        details_path = f"/v2/runs/{run['id']}"
        assert reference_client.get(details_path, headers=OWNER_A).json() == run
        assert reference_client.get(details_path, headers=OWNER_B).status_code == 404
    summaries = reference_client.get("/v2/runs", headers=OWNER_A).json()["runs"]
    assert [summary["agent_id"] for summary in summaries] == [
        "reference-vulnerable",
        "reference-corrected",
    ]
    assert all("results" not in summary for summary in summaries)
    assert reference_client.get("/v2/runs", headers=OWNER_B).json() == {"runs": []}


def test_v2_rolls_back_partial_results(reference_client: httpx2.Client, database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "ALTER TABLE agent_run_results ADD CONSTRAINT forced_failure CHECK (position < 1)"
        )
    failed = reference_client.post(
        "/v2/runs",
        json={"agent_id": "reference-corrected", "suite_id": TICKET_SUITE},
        headers=OWNER_A,
    )
    assert failed.status_code == 503
    assert reference_client.get("/v2/runs", headers=OWNER_A).json() == {"runs": []}
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM agent_run_results").fetchone() == (0,)
