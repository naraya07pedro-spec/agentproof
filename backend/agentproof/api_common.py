"""Workspace authentication and bounded request parsing used by both API versions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from typing import Annotated, Self, cast

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import ClientDisconnect

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
    reference_port: int = 8000
    model_workspace: str | None = None

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
        port = os.environ.get("AGENTPROOF_REFERENCE_PORT", "8000")
        if not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535:
            raise RuntimeError("AGENTPROOF_REFERENCE_PORT must be a TCP port number")
        model_workspace = None
        if os.environ.get("AGENTPROOF_MODEL_ENABLE_EXTERNAL") == "1":
            model_workspace = os.environ.get("AGENTPROOF_MODEL_WORKSPACE") or None
            if model_workspace is not None and model_workspace not in hashes:
                raise RuntimeError(
                    "AGENTPROOF_MODEL_WORKSPACE must identify a configured workspace"
                )
        return cls(database_url, hashes, int(port), model_workspace)


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
