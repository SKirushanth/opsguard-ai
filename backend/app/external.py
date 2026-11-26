"""Read compatible JSON only from operator-approved HTTPS destinations."""

import asyncio
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from .models import ServiceHealth, ServiceLogs, ServiceMetrics

MAX_BYTES = 65536
SCHEMAS = {"health": ServiceHealth, "logs": ServiceLogs, "metrics": ServiceMetrics}


def approved_origin(value: str) -> str:
    # Deliberately support DNS names on port 443 only.
    match = re.fullmatch(r"https://([a-zA-Z0-9.-]+)(?::443)?/?", value)
    if not match:
        raise HTTPException(422, "A plain HTTPS origin on port 443 is required.")
    host = match[1].lower()
    labels = host.split(".")
    if (
        len(labels) < 2
        or len(host) > 253
        or not labels[-1].isalpha()
        or host.endswith((".localhost", ".local", ".internal"))
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
    ):
        raise HTTPException(422, "A public DNS hostname is required.")
    origin = "https://" + host
    allowed = {
        item.strip().rstrip("/").lower()
        for item in os.getenv("APPROVED_HTTPS_ORIGINS", "").split(",")
    }
    if origin not in allowed:
        raise HTTPException(403, "This HTTPS origin is not approved by the operator.")
    return origin


def safe_path(value: str) -> str:
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_.~-]+/)*[a-zA-Z0-9_.~-]*", value) or any(
        s in (".", "..") for s in value.split("/")
    ):
        raise HTTPException(
            422, "Use an absolute endpoint path without traversal, query or fragment."
        )
    return value


async def resolve(host: str) -> str:
    answers = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM
    )
    addresses = [ipaddress.ip_address(a[4][0]) for a in answers]
    if not addresses or any(
        not a.is_global or a.is_multicast or a.is_reserved for a in addresses
    ):
        raise HTTPException(
            403, "The monitoring hostname resolved to a restricted network."
        )
    for address in addresses:
        if address.version == 4:
            return str(address)
    raise HTTPException(422, "A public IPv4 address is required for this integration.")


async def fetch_external(app: dict, resource: str):
    path = app[resource + "_path"]
    if not path:
        raise HTTPException(
            501,
            f"{resource.capitalize()} monitoring is unavailable for this application.",
        )
    origin = approved_origin(app["base_url"])
    host = urlsplit(origin).hostname
    path = safe_path(path)
    headers = {
        "Host": host,
        "Accept": "application/json",
        "Accept-Encoding": "identity",
    }
    try:
        tokens = json.loads(os.getenv("EXTERNAL_BEARER_TOKENS") or "{}")
        if not isinstance(tokens, dict) or not all(
            isinstance(v, str) for v in tokens.values()
        ):
            raise HTTPException(
                503, "Invalid external monitoring credential configuration."
            )
        token = tokens.get(origin + path)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with asyncio.timeout(5):
            ip = await resolve(host)
            # A fresh pool prevents cross-host connection reuse. The TCP target is
            # numeric while the original hostname is used for SNI and certificate validation.
            async with (
                httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=4) as client,
                client.stream(
                    "GET",
                    f"https://{ip}{path}",
                    headers=headers,
                    extensions={"sni_hostname": host},
                ) as response,
            ):
                if response.status_code != 200:
                    raise HTTPException(
                        502,
                        f"External {resource} endpoint returned HTTP {response.status_code}.",
                    )
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise HTTPException(
                        502, "Compressed monitoring responses are unsupported."
                    )
                data = bytearray()
                async for chunk in response.aiter_raw(chunk_size=8192):
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise HTTPException(
                            502, "External monitoring response exceeded 64 KiB."
                        )
                    data.extend(chunk)
        result = SCHEMAS[resource].model_validate_json(bytes(data))
        if result.service_id != app["local_id"]:
            raise HTTPException(
                502, "External monitoring returned a different service ID."
            )
        if token and token in result.model_dump_json():
            raise HTTPException(502, "Unsafe external monitoring response.")
        result.service_id = app["service_ids"][0]
        return result
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, "External monitoring timed out.") from exc
    except (OSError, httpx.RequestError, ValidationError, ValueError) as exc:
        raise HTTPException(
            502, "External monitoring failed or returned incompatible JSON."
        ) from exc
