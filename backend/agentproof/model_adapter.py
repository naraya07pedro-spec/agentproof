"""Bounded OpenAI Chat Completions transport for the V2 benchmark."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import socket
import ssl
from dataclasses import dataclass
from threading import Lock
from typing import Literal, NoReturn

import h11
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from .agent_v2 import (
    AdapterFailure,
    AgentMessage,
    AgentTurn,
    EvidenceSource,
    FailureCode,
    Label,
    TokenUsage,
    ToolCall,
)
from .http_adapter import HttpAgentEndpoint

ReferenceVariant = Literal["corrected", "vulnerable"]
REFERENCE_MODEL = "agentproof-ticket-reference"
REFERENCE_AGENT_VERSION = "2.0.0"
REFERENCE_AGENT_VERSIONS: dict[ReferenceVariant, str] = {
    "corrected": REFERENCE_AGENT_VERSION,
    "vulnerable": "2.0.0-vulnerable",
}
MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 128 * 1024
_MAX_HTTP_OVERHEAD = 16 * 1024
_MAX_ACTIVE = 4
_MAX_ATTEMPTS = 128
_MAX_CALL_SECONDS = 4.0
_MAX_MESSAGES = 32
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_TOOL_TOKEN = r"^[A-Za-z0-9_-]{1,64}$"
_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_ticket",
        "description": "Read a synthetic ticket from the current benchmark fixture.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}},
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
    },
}
_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "ticket_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "enum": ["billing", "technical", "general"]}
            },
            "required": ["label"],
            "additionalProperties": False,
        },
    },
}


class ModelConfigurationError(ValueError):
    """Trusted deployment configuration is missing or unsupported."""


@dataclass(frozen=True)
class _ReferenceRoute:
    variant: ReferenceVariant
    port: int

    @property
    def path(self) -> str:
        return f"/_reference/{self.variant}/v1/chat/completions"


def reference_adapter(
    variant: ReferenceVariant, *, port: int = 8000
) -> OpenAIChatCompletionsAdapter:
    if (
        variant not in ("corrected", "vulnerable")
        or type(port) is not int
        or not 1 <= port <= 65535
    ):
        raise ModelConfigurationError("Invalid reference variant or loopback port")
    return OpenAIChatCompletionsAdapter(
        _ReferenceRoute(variant, port),
        REFERENCE_MODEL,
        model_version=REFERENCE_AGENT_VERSIONS[variant],
    )


def external_adapter_from_environment() -> OpenAIChatCompletionsAdapter:
    if os.environ.get("AGENTPROOF_MODEL_ENABLE_EXTERNAL") != "1":
        raise ModelConfigurationError("External model HTTP is disabled")
    try:
        token = os.environ.get("AGENTPROOF_MODEL_TOKEN")
        endpoint = HttpAgentEndpoint(
            hostname=os.environ["AGENTPROOF_MODEL_HOSTNAME"],
            ipv4_address=os.environ["AGENTPROOF_MODEL_IPV4"],
            path=os.environ["AGENTPROOF_MODEL_PATH"],
            bearer_token=SecretStr(token) if token is not None else None,
        )
        return OpenAIChatCompletionsAdapter(
            endpoint,
            os.environ["AGENTPROOF_MODEL_NAME"],
            model_version=os.environ.get("AGENTPROOF_MODEL_VERSION"),
            allow_external=True,
        )
    except (KeyError, ValidationError, OSError):
        pass
    raise ModelConfigurationError("Invalid external model endpoint configuration")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> NoReturn:
    raise ValueError("nonfinite JSON number")


def parse_model_json(raw: bytes | str) -> object:
    """Parse the private provider payload without echoing it."""
    return json.loads(
        raw.decode("utf-8") if isinstance(raw, bytes) else raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def _message_payload(message: AgentMessage) -> dict[str, object]:
    result: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    return result


class _WireModel(BaseModel):
    # Ignore provider metadata such as native token detail subfields.
    model_config = ConfigDict(extra="ignore", strict=True, hide_input_in_errors=True)


class _WireFunction(_WireModel):
    name: str = Field(pattern=_TOOL_TOKEN)
    arguments: str = Field(max_length=2048)


class _WireToolCall(_WireModel):
    id: str = Field(pattern=_TOOL_TOKEN)
    type: Literal["function"]
    function: _WireFunction


class _WireMessage(_WireModel):
    role: Literal["assistant"]
    content: str | None = Field(default=None, max_length=2048, repr=False)
    tool_calls: list[_WireToolCall] | None = Field(default=None, max_length=4)
    refusal: str | None = Field(default=None, max_length=2048, repr=False)
    function_call: object | None = Field(default=None, repr=False)


class _WireChoice(_WireModel):
    index: Literal[0]
    finish_reason: Literal["stop", "tool_calls"]
    message: _WireMessage


class _WireUsage(_WireModel):
    prompt_tokens: int = Field(ge=0, le=131_072)
    completion_tokens: int = Field(ge=0, le=256)


class _WireResponse(_WireModel):
    choices: list[_WireChoice] = Field(min_length=1, max_length=1)
    usage: _WireUsage | None = None


class _WireFinalLabel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    label: Label


def _parse_turn(body: bytes) -> AgentTurn:
    result = _WireResponse.model_validate(parse_model_json(body))
    choice = result.choices[0]
    message = choice.message
    usage = (
        TokenUsage(
            input_tokens=result.usage.prompt_tokens, output_tokens=result.usage.completion_tokens
        )
        if result.usage is not None
        else None
    )
    if message.refusal is not None or message.function_call is not None:
        raise AdapterFailure("malformed_response")
    if choice.finish_reason == "tool_calls":
        if not message.tool_calls or message.content not in (None, ""):
            raise AdapterFailure("malformed_response")
        return AgentTurn(
            tool_calls=tuple(
                ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
                for call in message.tool_calls
            ),
            usage=usage,
        )
    if message.tool_calls or message.content is None:
        raise AdapterFailure("malformed_response")
    label = _WireFinalLabel.model_validate(parse_model_json(message.content))
    return AgentTurn(label=label.label, usage=usage)


class OpenAIChatCompletionsAdapter:
    """Serialize an engine conversation to a fixed Chat Completions route."""

    adapter_version = "openai-chat-completions/1.0"

    def __init__(
        self,
        destination: _ReferenceRoute | HttpAgentEndpoint,
        model_id: str,
        *,
        model_version: str | None = None,
        allow_external: bool = False,
    ) -> None:
        if not _MODEL_ID.fullmatch(model_id):
            raise ModelConfigurationError("Invalid model name")
        if model_version is not None and not _MODEL_ID.fullmatch(model_version):
            raise ModelConfigurationError("Invalid declared model version")
        self.model_id = model_id
        self.model_version = model_version
        self.evidence: EvidenceSource
        self._tls: ssl.SSLContext | None = None
        if isinstance(destination, _ReferenceRoute):
            self.evidence = "local_reference_http"
            self._address = self._hostname = "127.0.0.1"
            self._port = destination.port
            self._token = None
        else:
            if allow_external is not True:
                raise ModelConfigurationError("External model HTTP is disabled")
            self.evidence = "external_model_http"
            self._address = destination.ipv4_address
            self._hostname = destination.hostname
            self._port = 443
            self._token = destination.bearer_token
            self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._tls.minimum_version = ssl.TLSVersion.TLSv1_2
            self._tls.load_default_certs()
        self._path = destination.path
        self._lock = Lock()
        self._attempts = 0
        self._active = 0

    async def respond(self, messages: tuple[AgentMessage, ...], *, timeout_s: float) -> AgentTurn:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise AdapterFailure("timeout")
        with self._lock:
            if self._attempts >= _MAX_ATTEMPTS or self._active >= _MAX_ACTIVE:
                raise AdapterFailure("unavailable")
            self._attempts += 1
            self._active += 1
        error: FailureCode
        try:
            async with asyncio.timeout(min(timeout_s, _MAX_CALL_SECONDS)):
                return await self._respond(messages)
        except AdapterFailure as failure:
            error = failure.code
        except TimeoutError:
            error = "timeout"
        except ssl.SSLError:
            error = "unavailable"
        except OSError:
            error = "transient"
        except (h11.ProtocolError, ValueError, RecursionError):
            error = "malformed_response"
        finally:
            with self._lock:
                self._active -= 1
        raise AdapterFailure(error)

    async def _respond(self, messages: tuple[AgentMessage, ...]) -> AgentTurn:
        if not 1 <= len(messages) <= _MAX_MESSAGES:
            raise AdapterFailure("malformed_response")
        body = json.dumps(
            {
                "model": self.model_id,
                "messages": [_message_payload(message) for message in messages],
                "tools": [_TOOL_SCHEMA],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "response_format": _RESPONSE_FORMAT,
                "max_completion_tokens": 256,
                "stream": False,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(body) > MAX_REQUEST_BYTES:
            raise AdapterFailure("malformed_response")
        reader, writer = await asyncio.open_connection(
            self._address,
            self._port,
            ssl=self._tls,
            server_hostname=self._hostname if self._tls is not None else None,
            family=socket.AF_INET,
            flags=socket.AI_NUMERICHOST,
            limit=8192,
        )
        try:
            protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=_MAX_HTTP_OVERHEAD)
            host = self._hostname if self._port == 443 else f"{self._hostname}:{self._port}"
            headers = [
                ("Host", host),
                ("Accept", "application/json"),
                ("Accept-Encoding", "identity"),
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
                ("Connection", "close"),
            ]
            if self._token is not None:
                headers.append(("Authorization", f"Bearer {self._token.get_secret_value()}"))
            writer.write(
                protocol.send(h11.Request(method="POST", target=self._path, headers=headers)) or b""
            )
            writer.write(protocol.send(h11.Data(data=body)) or b"")
            writer.write(protocol.send(h11.EndOfMessage()) or b"")
            await writer.drain()
            return _parse_turn(await _read_response(protocol, reader))
        finally:
            writer.transport.abort()


async def _read_response(protocol: h11.Connection, reader: asyncio.StreamReader) -> bytes:
    wire_bytes = 0
    body = bytearray()
    while True:
        event = protocol.next_event()
        if event is h11.NEED_DATA:
            chunk = await reader.read(4096)
            wire_bytes += len(chunk)
            if wire_bytes > MAX_RESPONSE_BYTES + _MAX_HTTP_OVERHEAD:
                raise AdapterFailure("malformed_response")
            protocol.receive_data(chunk)
        elif isinstance(event, h11.Response):
            _check_response_headers(event)
        elif isinstance(event, h11.Data):
            if len(body) + len(event.data) > MAX_RESPONSE_BYTES:
                raise AdapterFailure("malformed_response")
            body.extend(event.data)
        elif isinstance(event, h11.EndOfMessage):
            if event.headers or protocol.trailing_data[0]:
                raise AdapterFailure("malformed_response")
            return bytes(body)
        else:
            raise AdapterFailure("malformed_response")


def _check_response_headers(response: h11.Response) -> None:
    status = response.status_code
    if status in (408, 425, 429) or 500 <= status <= 599:
        raise AdapterFailure("transient")
    if status != 200:
        raise AdapterFailure("unavailable")
    headers: dict[bytes, bytes] = {}
    for key, value in response.headers:
        if key in headers:
            raise AdapterFailure("malformed_response")
        headers[key] = value.lower()
    content_type = headers.get(b"content-type", b"").partition(b";")[0].strip()
    if content_type != b"application/json":
        raise AdapterFailure("malformed_response")
    if headers.get(b"content-encoding", b"identity") != b"identity":
        raise AdapterFailure("malformed_response")
    content_length = headers.get(b"content-length")
    if content_length is not None and (
        b"transfer-encoding" in headers or int(content_length) > MAX_RESPONSE_BYTES
    ):
        raise AdapterFailure("malformed_response")
