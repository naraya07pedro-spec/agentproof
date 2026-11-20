"""Read a diagnostic observation from one operator-approved HTTPS endpoint."""

import asyncio
import ipaddress
import json
import re
import socket
import ssl
from threading import Lock
from typing import ClassVar, Literal

import h11
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_PATH = re.compile(r"/(?:[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*)?")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_MAX_HTTP_OVERHEAD = 16_384
_CONTENT_TYPES = {b"application/json", b"application/json; charset=utf-8"}
_SPECIAL_USE = (
    ipaddress.IPv4Network("192.0.0.0/24"),
    ipaddress.IPv4Network("192.88.99.0/24"),
)

ErrorCode = Literal[
    "disabled",
    "busy",
    "budget_exhausted",
    "deadline_exceeded",
    "transport_error",
    "invalid_response",
    "response_too_large",
]


class HttpAdapterError(Exception):
    """Expose stable error codes without including credentials or provider contents."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)


class HttpAgentEndpoint(BaseModel):
    """Trusted configuration. Never construct this from suite data or agent output."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    hostname: str = Field(max_length=253)
    ipv4_address: str
    path: str = Field(max_length=256)
    bearer_token: SecretStr | None = Field(default=None, repr=False)

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, value: str) -> str:
        labels = value.split(".")
        if len(labels) < 2 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
            raise ValueError("expected a lower-case fully qualified DNS name")
        if not any(character.isalpha() for character in labels[-1]):
            raise ValueError("expected a DNS name with an alphabetic top-level label")
        return value

    @field_validator("ipv4_address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        try:
            address = ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError:
            raise ValueError("expected a public IPv4 address") from None
        if (
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or any(address in network for network in _SPECIAL_USE)
        ):
            raise ValueError("expected a public unicast IPv4 address")
        return str(address)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not _PATH.fullmatch(value) or set(value.split("/")).intersection({".", ".."}):
            raise ValueError("expected an absolute literal path without query or fragment")
        return value

    @field_validator("bearer_token")
    @classmethod
    def validate_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            token = value.get_secret_value()
            if len(token) > 1024 or not _TOKEN.fullmatch(token):
                raise ValueError("expected a valid bearer credential")
        return value


class HttpAdapterLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    timeout_seconds: float = Field(default=2.0, ge=0.01, le=10)
    max_response_bytes: int = Field(default=8192, ge=64, le=32_768)
    max_requests: int = Field(default=4, ge=1, le=32)
    max_concurrency: int = Field(default=1, ge=1, le=4)


class HttpAgentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    evidence: ClassVar[Literal["external_http"]] = "external_http"
    version: Literal["1"]
    outcome: Literal["completed", "refused"]
    answer: str = Field(max_length=256, repr=False)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate JSON key")
        values[key] = value
    return values


class HttpAgentAdapter:
    """A bounded, read-only SDK probe. Each instance has a lifetime request budget."""

    def __init__(
        self,
        endpoint: HttpAgentEndpoint | None = None,
        *,
        enabled: bool = False,
        limits: HttpAdapterLimits | None = None,
    ) -> None:
        self._endpoint = endpoint if enabled is True else None
        self._limits = limits if limits is not None else HttpAdapterLimits()
        self._lock = Lock()
        self._active = 0
        self._attempts = 0
        self._tls = self._create_tls_context() if self._endpoint is not None else None

    @staticmethod
    def _create_tls_context() -> ssl.SSLContext:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_default_certs()
        return tls

    async def read_observation(self) -> HttpAgentObservation:
        endpoint = self._endpoint
        tls = self._tls
        if endpoint is None or tls is None:
            raise HttpAdapterError("disabled")
        with self._lock:
            if self._attempts >= self._limits.max_requests:
                raise HttpAdapterError("budget_exhausted")
            if self._active >= self._limits.max_concurrency:
                raise HttpAdapterError("busy")
            self._attempts += 1
            self._active += 1
        error: ErrorCode
        try:
            async with asyncio.timeout(self._limits.timeout_seconds):
                return await self._request(endpoint, tls)
        except HttpAdapterError as failure:
            error = failure.code
        except TimeoutError:
            error = "deadline_exceeded"
        except OSError:
            error = "transport_error"
        except h11.ProtocolError:
            error = "invalid_response"
        finally:
            with self._lock:
                self._active -= 1
        raise HttpAdapterError(error)

    async def _request(
        self, endpoint: HttpAgentEndpoint, tls: ssl.SSLContext
    ) -> HttpAgentObservation:
        reader, writer = await asyncio.open_connection(
            endpoint.ipv4_address,
            443,
            ssl=tls,
            server_hostname=endpoint.hostname,
            family=socket.AF_INET,
            flags=socket.AI_NUMERICHOST,
            limit=8192,
        )
        try:
            protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=_MAX_HTTP_OVERHEAD)
            headers = [
                ("Host", endpoint.hostname),
                ("Accept", "application/json"),
                ("Accept-Encoding", "identity"),
                ("Connection", "close"),
            ]
            if endpoint.bearer_token is not None:
                headers.append(
                    ("Authorization", "Bearer " + endpoint.bearer_token.get_secret_value())
                )
            writer.write(
                protocol.send(h11.Request(method="GET", target=endpoint.path, headers=headers))
                or b""
            )
            writer.write(protocol.send(h11.EndOfMessage()) or b"")
            await writer.drain()
            return await self._read_response(protocol, reader)
        finally:
            writer.transport.abort()

    async def _read_response(
        self, protocol: h11.Connection, reader: asyncio.StreamReader
    ) -> HttpAgentObservation:
        total_wire_bytes = 0
        body = bytearray()
        saw_headers = False
        while True:
            event = protocol.next_event()
            if event is h11.NEED_DATA:
                chunk = await reader.read(4096)
                total_wire_bytes += len(chunk)
                if total_wire_bytes > self._limits.max_response_bytes + _MAX_HTTP_OVERHEAD:
                    raise HttpAdapterError("response_too_large")
                protocol.receive_data(chunk)
            elif isinstance(event, h11.Response):
                self._check_response_headers(event)
                saw_headers = True
            elif isinstance(event, h11.Data):
                if len(body) + len(event.data) > self._limits.max_response_bytes:
                    raise HttpAdapterError("response_too_large")
                body.extend(event.data)
            elif isinstance(event, h11.EndOfMessage) and saw_headers:
                if event.headers or protocol.trailing_data[0]:
                    raise HttpAdapterError("invalid_response")
                try:
                    parsed = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
                    return HttpAgentObservation.model_validate(parsed)
                except (ValueError, RecursionError):
                    raise HttpAdapterError("invalid_response") from None
            else:
                raise HttpAdapterError("invalid_response")

    def _check_response_headers(self, response: h11.Response) -> None:
        if response.status_code != 200:
            raise HttpAdapterError("invalid_response")
        headers: dict[bytes, bytes] = {}
        for key, value in response.headers:
            if key in headers:
                raise HttpAdapterError("invalid_response")
            headers[key] = value.lower()
        if headers.get(b"content-type") not in _CONTENT_TYPES:
            raise HttpAdapterError("invalid_response")
        if headers.get(b"content-encoding", b"identity") != b"identity":
            raise HttpAdapterError("invalid_response")
        content_length = headers.get(b"content-length")
        if content_length is not None:
            if b"transfer-encoding" in headers:
                raise HttpAdapterError("invalid_response")
            if int(content_length) > self._limits.max_response_bytes:
                raise HttpAdapterError("response_too_large")
