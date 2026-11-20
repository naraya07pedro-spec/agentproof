from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agentproof import api as api_module
from agentproof.api import Settings, create_app
from agentproof.db import RUN_LOCK
from agentproof.migrate import MigrationError, migrate

EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "examples" / "defensive-suite.json"
TOKEN_A = "demo_test_token_for_workspace_a"
TOKEN_B = "demo_test_token_for_workspace_b"
OWNER_A = {"Authorization": f"Bearer {TOKEN_A}"}
OWNER_B = {"Authorization": f"Bearer {TOKEN_B}"}


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
    monkeypatch.setattr(api_module, "BODY_READ_SECONDS", 0.01)
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
    assert migrate(database_url) == ["001_initial.sql"]
    assert migrate(database_url) == []
    with TestClient(create_app(settings(database_url))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        with psycopg.connect(database_url) as connection:
            connection.execute("UPDATE schema_migrations SET checksum = 'tampered'")
        assert client.get("/healthz").status_code == 503
    with pytest.raises(MigrationError, match="checksum mismatch"):
        migrate(database_url)


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
