import asyncio
import socket
import ssl
from ssl import CERT_REQUIRED, TLSVersion

import pytest
from pydantic import SecretStr, ValidationError

from agentproof.http_adapter import (
    HttpAdapterError,
    HttpAdapterLimits,
    HttpAgentAdapter,
    HttpAgentEndpoint,
)

OBSERVATION = b'{"version":"1","outcome":"completed","answer":"synthetic"}'


class RecordingWriter:
    def __init__(self) -> None:
        self.transport = self
        self.aborted = False
        self.request = bytearray()

    def write(self, data: bytes) -> None:
        self.request.extend(data)

    async def drain(self) -> None:
        pass

    def abort(self) -> None:
        self.aborted = True


def endpoint() -> HttpAgentEndpoint:
    return HttpAgentEndpoint(
        hostname="fixture.example.com",
        ipv4_address="1.1.1.1",
        path="/agent/observation",
        bearer_token=SecretStr("fixture-test-credential"),
    )


def response(
    body: bytes = OBSERVATION,
    status: bytes = b"200 OK",
    headers: bytes = b"",
    content_type: bytes = b"application/json",
) -> bytes:
    return (
        b"HTTP/1.1 " + status + b"\r\n"
        b"Content-Type: " + content_type + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n" + headers + b"\r\n" + body
    )


def fake_response(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> RecordingWriter:
    writer = RecordingWriter()

    async def connect(
        host: str,
        port: int,
        *,
        ssl: ssl.SSLContext,
        server_hostname: str,
        family: int,
        flags: int,
        limit: int,
    ) -> tuple[asyncio.StreamReader, RecordingWriter]:
        assert (host, port, server_hostname) == ("1.1.1.1", 443, "fixture.example.com")
        assert (family, flags, limit) == (socket.AF_INET, socket.AI_NUMERICHOST, 8192)
        assert ssl.check_hostname and ssl.verify_mode == CERT_REQUIRED
        assert ssl.minimum_version == TLSVersion.TLSv1_2
        reader = asyncio.StreamReader()
        reader.feed_data(payload)
        reader.feed_eof()
        return reader, writer

    monkeypatch.setattr(asyncio, "open_connection", connect)
    return writer


def test_default_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = fake_response(monkeypatch, response())
    with pytest.raises(HttpAdapterError, match="^disabled$"):
        asyncio.run(HttpAgentAdapter(endpoint()).read_observation())
    assert not writer.request and not writer.aborted


def test_exact_destination_read_only_credential_and_sanitization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = endpoint()
    writer = fake_response(monkeypatch, response())
    result = asyncio.run(HttpAgentAdapter(configured, enabled=True).read_observation())
    assert result.answer == "synthetic"
    assert result.evidence == "external_http"
    assert "synthetic" not in repr(result)
    assert "fixture-test-credential" not in repr(configured)
    assert writer.request.startswith(b"GET /agent/observation HTTP/1.1\r\n")
    assert b"host: fixture.example.com\r\n" in writer.request.lower()
    assert b"authorization: bearer fixture-test-credential\r\n" in writer.request.lower()
    assert writer.request.endswith(b"\r\n\r\n") and writer.aborted


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ipv4_address", "127.0.0.1"),
        ("ipv4_address", "10.0.0.1"),
        ("ipv4_address", "100.100.100.200"),
        ("ipv4_address", "169.254.169.254"),
        ("ipv4_address", "192.0.0.8"),
        ("ipv4_address", "224.0.0.1"),
        ("ipv4_address", "0.0.0.0"),
        ("ipv4_address", "::ffff:1.1.1.1"),
        ("hostname", "fixture.example.com:8443"),
        ("hostname", "fixture.example.com@another.example.com"),
        ("hostname", "1.1.1.1"),
        ("path", "//another.example.com"),
        ("path", "/agent/../unapproved"),
        ("path", "/agent?destination=another.example.com"),
        ("path", "/agent%0A"),
        ("path", "/agent#fragment"),
        ("bearer_token", SecretStr("hidden\r\nheader")),
    ],
)
def test_rejects_unsafe_endpoint_configuration(field: str, value: object) -> None:
    settings = endpoint().model_dump()
    settings[field] = value
    with pytest.raises(ValidationError) as denied:
        HttpAgentEndpoint.model_validate(settings)
    assert denied.value.errors(include_input=False)[0]["loc"] == (field,)
    assert "hidden" not in str(denied.value)


@pytest.mark.parametrize(
    "payload",
    [
        response(status=b"302 Found", headers=b"Location: https://other.example.com/\r\n"),
        response(body=b"synthetic-private-data"),
        response(body=b'{"version":"0","version":"1","outcome":"completed","answer":"synthetic"}'),
        response(body=b'{"version":"1","outcome":"completed","private":"synthetic-private-data"}'),
        response(headers=b"Content-Encoding: gzip\r\n"),
        response(content_type=b"text/plain"),
        response(headers=b"Content-Type: application/json\r\n"),
        b"malformed protocol synthetic-private-data",
    ],
)
def test_denies_unexpected_responses_without_echoing_content(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    writer = fake_response(monkeypatch, payload)
    with pytest.raises(HttpAdapterError, match="^invalid_response$") as denied:
        asyncio.run(HttpAgentAdapter(endpoint(), enabled=True).read_observation())
    assert denied.value.__context__ is None
    assert writer.aborted


@pytest.mark.parametrize(
    "payload",
    [
        response(body=b"o" * 65),
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n41\r\n" + b"o" * 65 + b"\r\n0\r\n\r\n",
    ],
)
def test_response_limit(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    writer = fake_response(monkeypatch, payload)
    adapter = HttpAgentAdapter(
        endpoint(), enabled=True, limits=HttpAdapterLimits(max_response_bytes=64)
    )
    with pytest.raises(HttpAdapterError, match="^response_too_large$"):
        asyncio.run(adapter.read_observation())
    assert writer.aborted


def test_wire_budget_bounds_http_overhead(monkeypatch: pytest.MonkeyPatch) -> None:
    # Chunk extensions consume wire bytes without increasing the application body.
    extensions = b"1;" + b"test" * 2_000 + b"\r\no\r\n"
    payload = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n" + extensions * 3 + b"0\r\n\r\n"
    )
    writer = fake_response(monkeypatch, payload)
    adapter = HttpAgentAdapter(
        endpoint(), enabled=True, limits=HttpAdapterLimits(max_response_bytes=64)
    )
    with pytest.raises(HttpAdapterError, match="^response_too_large$"):
        asyncio.run(adapter.read_observation())
    assert writer.aborted


def test_timeout_consumes_request_budget_and_releases_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = RecordingWriter()

    async def connect(
        *args: object, **kwargs: object
    ) -> tuple[asyncio.StreamReader, RecordingWriter]:
        return asyncio.StreamReader(), writer

    monkeypatch.setattr(asyncio, "open_connection", connect)
    adapter = HttpAgentAdapter(
        endpoint(), enabled=True, limits=HttpAdapterLimits(timeout_seconds=0.01, max_requests=1)
    )
    with pytest.raises(HttpAdapterError, match="^deadline_exceeded$"):
        asyncio.run(adapter.read_observation())
    assert writer.aborted
    with pytest.raises(HttpAdapterError, match="^budget_exhausted$"):
        asyncio.run(adapter.read_observation())


def test_concurrency_saturation_and_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def verify() -> None:
        opened = asyncio.Event()
        cancelled_writer = RecordingWriter()

        async def connect(
            *args: object, **kwargs: object
        ) -> tuple[asyncio.StreamReader, RecordingWriter]:
            opened.set()
            return asyncio.StreamReader(), cancelled_writer

        monkeypatch.setattr(asyncio, "open_connection", connect)
        adapter = HttpAgentAdapter(
            endpoint(), enabled=True, limits=HttpAdapterLimits(max_requests=2, max_concurrency=1)
        )
        active = asyncio.create_task(adapter.read_observation())
        await asyncio.wait_for(opened.wait(), timeout=1)
        with pytest.raises(HttpAdapterError, match="^busy$"):
            await adapter.read_observation()
        active.cancel()
        with pytest.raises(asyncio.CancelledError):
            await active
        assert cancelled_writer.aborted
        writer = fake_response(monkeypatch, response())
        assert (await adapter.read_observation()).outcome == "completed"
        assert writer.aborted
        with pytest.raises(HttpAdapterError, match="^budget_exhausted$"):
            await adapter.read_observation()

    asyncio.run(verify())


def test_tls_verification_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def connect(*args: object, **kwargs: object) -> None:
        raise ssl.SSLCertVerificationError("synthetic-private-data")

    monkeypatch.setattr(asyncio, "open_connection", connect)
    with pytest.raises(HttpAdapterError, match="^transport_error$") as denied:
        asyncio.run(HttpAgentAdapter(endpoint(), enabled=True).read_observation())
    assert denied.value.__context__ is None
