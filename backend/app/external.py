"""Read compatible JSON and bounded plain-text monitoring data from approved HTTPS origins."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ValidationError

from .models import NormalizedStatus, ServiceHealth
from .normalizer import (
    CustomHealthMapping,
    ServiceIdMismatchError,
    normalize_health_response,
    normalize_logs_response,
    normalize_metrics_response,
)

MAX_BYTES = 65536


class ConnectionTestResult(BaseModel):
    connected: bool
    connection_result: str
    http_status: int | None = None
    sanitized_response: dict[str, Any] | list[Any] | str | None = None
    normalized_status: NormalizedStatus
    normalization_method: str
    explanation: str
    reason: str | None = None
    requires_mapping: bool = False
    error_category: str | None = None
    checked_at: AwareDatetime


def configured_secrets() -> list[str]:
    secrets_list: list[str] = []
    for env_name in (
        "OPERATOR_PASSWORD",
        "MONITORING_API_KEY",
        "AGENT_API_KEY",
        "LLM_API_KEY",
    ):
        val = os.getenv(env_name, "").strip()
        if val:
            secrets_list.append(val)
    try:
        tokens = json.loads(os.getenv("EXTERNAL_BEARER_TOKENS") or "{}")
        if isinstance(tokens, dict):
            for v in tokens.values():
                if isinstance(v, str) and v.strip():
                    secrets_list.append(v.strip())
    except ValueError:
        pass
    return secrets_list


def _bearer_token_for(endpoint_url: str) -> str | None:
    try:
        tokens = json.loads(os.getenv("EXTERNAL_BEARER_TOKENS") or "{}")
    except ValueError as exc:
        raise HTTPException(
            503, "Invalid external monitoring credential configuration."
        ) from exc
    if not isinstance(tokens, dict) or not all(
        isinstance(v, str) for v in tokens.values()
    ):
        raise HTTPException(
            503, "Invalid external monitoring credential configuration."
        )
    return tokens.get(endpoint_url)


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


async def _read_pinned_get(
    *, origin: str, host: str, path: str, accept: str, token: str | None
) -> tuple[int, str, bytes]:
    headers = {
        "Host": host,
        "Accept": accept,
        "Accept-Encoding": "identity",
    }
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
            content_type = response.headers.get("content-type", "")
            return response.status_code, content_type, bytes(data)


async def fetch_external(app: dict, resource: str, *, strict_validate: bool = False):
    path = app.get(resource + "_path")
    if not path:
        raise HTTPException(
            501,
            f"{resource.capitalize()} monitoring is unavailable for this application.",
        )
    origin = approved_origin(app["base_url"])
    host = urlsplit(origin).hostname or ""
    path = safe_path(path)
    token = _bearer_token_for(origin + path)
    secrets_list = configured_secrets()
    accept = (
        "application/json, text/plain;q=0.9"
        if resource == "logs"
        else "application/json"
    )

    if resource == "health":
        raw_mapping = app.get("health_mapping")
        mapping = (
            CustomHealthMapping.model_validate(raw_mapping)
            if raw_mapping is not None
            else None
        )
        try:
            status_code, _content_type, body = await _read_pinned_get(
                origin=origin, host=host, path=path, accept=accept, token=token
            )
            result = normalize_health_response(
                service_id=app["service_ids"][0],
                http_status=status_code,
                body=body,
                mapping=mapping,
                secret_values=secrets_list,
                expected_local_id=app["local_id"],
            )
        except HTTPException as exc:
            if strict_validate or exc.status_code == 403:
                raise
            result = normalize_health_response(
                service_id=app["service_ids"][0],
                http_status=None,
                body=None,
                network_error=str(exc.detail),
                mapping=mapping,
                secret_values=secrets_list,
            )
        except (TimeoutError, httpx.TimeoutException) as exc:
            if strict_validate:
                raise HTTPException(504, "External monitoring timed out.") from exc
            result = normalize_health_response(
                service_id=app["service_ids"][0],
                http_status=None,
                body=None,
                network_error="External monitoring timed out",
                mapping=mapping,
                secret_values=secrets_list,
            )
        except ServiceIdMismatchError as exc:
            raise HTTPException(502, str(exc)) from exc
        except (OSError, httpx.RequestError, ValidationError, ValueError) as exc:
            if strict_validate:
                raise HTTPException(
                    502, "External monitoring failed or returned incompatible JSON."
                ) from exc
            result = normalize_health_response(
                service_id=app["service_ids"][0],
                http_status=None,
                body=None,
                network_error="External monitoring endpoint could not be reached",
                mapping=mapping,
                secret_values=secrets_list,
            )

        if token and token in result.model_dump_json():
            raise HTTPException(502, "Unsafe external monitoring response.")

        if strict_validate and (
            result.error_category is not None
            or result.requires_mapping
            or (result.http_status is not None and result.http_status != 200 and result.status == "unknown")
        ):
            raise HTTPException(502, result.explanation or result.message)
        return result

    try:
        status_code, content_type, body = await _read_pinned_get(
            origin=origin, host=host, path=path, accept=accept, token=token
        )
        if status_code != 200:
            raise HTTPException(
                502,
                f"External {resource} endpoint returned HTTP {status_code}.",
            )
        if resource == "logs":
            result = normalize_logs_response(
                service_id=app["service_ids"][0],
                body=body,
                content_type=content_type,
                secret_values=secrets_list,
                expected_local_id=app["local_id"],
            )
        else:
            result = normalize_metrics_response(
                service_id=app["service_ids"][0],
                body=body,
                secret_values=secrets_list,
                expected_local_id=app["local_id"],
            )
        if token and token in result.model_dump_json():
            raise HTTPException(502, "Unsafe external monitoring response.")
        return result
    except HTTPException:
        raise
    except ServiceIdMismatchError as exc:
        raise HTTPException(502, str(exc)) from exc
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, "External monitoring timed out.") from exc
    except (OSError, httpx.RequestError, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(
            502, "External monitoring failed or returned incompatible JSON."
        ) from exc


async def probe_health_connection(
    *,
    base_url: str,
    service_id: str,
    health_path: str,
    logs_path: str | None = None,
    metrics_path: str | None = None,
    mapping: CustomHealthMapping | None = None,
) -> ConnectionTestResult:
    """Perform a read-only SSRF-checked connection test without persisting or calling any LLM."""
    origin = approved_origin(base_url)
    host = urlsplit(origin).hostname or ""
    path = safe_path(health_path)
    if logs_path is not None:
        safe_path(logs_path)
    if metrics_path is not None:
        safe_path(metrics_path)

    token = _bearer_token_for(origin + path)
    secrets_list = configured_secrets()
    now = datetime.now(UTC)

    try:
        status_code, _content_type, body = await _read_pinned_get(
            origin=origin,
            host=host,
            path=path,
            accept="application/json",
            token=token,
        )
    except HTTPException as exc:
        if exc.status_code == 403:
            raise
        norm = normalize_health_response(
            service_id=service_id,
            http_status=None,
            body=None,
            network_error=str(exc.detail),
            mapping=mapping,
            secret_values=secrets_list,
        )
        return ConnectionTestResult(
            connected=False,
            connection_result=f"Connection failed: {exc.detail}",
            http_status=None,
            sanitized_response=None,
            normalized_status=norm.status,
            normalization_method=norm.normalization_method,
            explanation=norm.explanation or norm.message,
            reason=norm.reason,
            requires_mapping=norm.requires_mapping,
            error_category=norm.error_category,
            checked_at=norm.last_checked_at or now,
        )
    except (TimeoutError, httpx.TimeoutException):
        norm = normalize_health_response(
            service_id=service_id,
            http_status=None,
            body=None,
            network_error="Monitoring endpoint timed out",
            mapping=mapping,
            secret_values=secrets_list,
        )
        return ConnectionTestResult(
            connected=False,
            connection_result="Connection failed: timed out",
            http_status=None,
            sanitized_response=None,
            normalized_status=norm.status,
            normalization_method=norm.normalization_method,
            explanation=norm.explanation or norm.message,
            reason=norm.reason,
            requires_mapping=norm.requires_mapping,
            error_category=norm.error_category,
            checked_at=norm.last_checked_at or now,
        )
    except (OSError, httpx.RequestError):
        norm = normalize_health_response(
            service_id=service_id,
            http_status=None,
            body=None,
            network_error="Monitoring endpoint unreachable",
            mapping=mapping,
            secret_values=secrets_list,
        )
        return ConnectionTestResult(
            connected=False,
            connection_result="Connection failed: endpoint unreachable",
            http_status=None,
            sanitized_response=None,
            normalized_status=norm.status,
            normalization_method=norm.normalization_method,
            explanation=norm.explanation or norm.message,
            reason=norm.reason,
            requires_mapping=norm.requires_mapping,
            error_category=norm.error_category,
            checked_at=norm.last_checked_at or now,
        )

    try:
        norm = normalize_health_response(
            service_id=service_id,
            http_status=status_code,
            body=body,
            mapping=mapping,
            secret_values=secrets_list,
            expected_local_id=service_id,
        )
    except ServiceIdMismatchError as exc:
        return ConnectionTestResult(
            connected=True,
            connection_result=f"Connected (HTTP {status_code}), but service_id mismatched",
            http_status=status_code,
            sanitized_response=None,
            normalized_status="unknown",
            normalization_method="http_error",
            explanation=str(exc),
            reason=str(exc),
            requires_mapping=False,
            error_category="endpoint_error",
            checked_at=now,
        )

    if status_code in (401, 403):
        conn_msg = f"Authentication failed (HTTP {status_code})"
    elif 300 <= status_code < 400:
        conn_msg = f"Blocked redirect (HTTP {status_code})"
    elif status_code >= 400:
        conn_msg = f"Endpoint error (HTTP {status_code})"
    else:
        conn_msg = f"Connected (HTTP {status_code})"

    return ConnectionTestResult(
        connected=status_code < 400 and norm.error_category != "endpoint_error",
        connection_result=conn_msg,
        http_status=status_code,
        sanitized_response=norm.evidence,
        normalized_status=norm.status,
        normalization_method=norm.normalization_method,
        explanation=norm.explanation or norm.message,
        reason=norm.reason,
        requires_mapping=norm.requires_mapping,
        error_category=norm.error_category,
        checked_at=norm.last_checked_at or now,
    )


def parse_health(
    data: bytes, service_id: str, mapping: CustomHealthMapping | None = None
) -> ServiceHealth:
    return normalize_health_response(
        service_id=service_id,
        http_status=200,
        body=data,
        mapping=mapping,
        secret_values=configured_secrets(),
    )
