import asyncio
import json
import sqlite3
import uuid
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import external, services
from .access import operator, viewer
from .database import database
from .models import NormalizedStatus, ServiceHealth
from .normalizer import (
    CustomHealthMapping,
    normalize_health_response,
)

router = APIRouter(prefix="/api/applications", tags=["applications"])
PathText = Annotated[str, Field(max_length=256)]


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    base_url: Annotated[str, Field(max_length=260)]
    service_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
    health_path: PathText
    logs_path: PathText | None = None
    metrics_path: PathText | None = None
    health_mapping: CustomHealthMapping | None = None


class ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: Annotated[str, Field(max_length=260)]
    service_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,39}$")] = "probe"
    health_path: PathText
    logs_path: PathText | None = None
    metrics_path: PathText | None = None
    health_mapping: CustomHealthMapping | None = None


class MappingPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mapping: CustomHealthMapping
    sample_response: dict[str, Any] | list[Any] | str | None = None
    http_status: int = Field(default=200, ge=100, le=599)


class MappingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    health_mapping: CustomHealthMapping | None = None


class Application(BaseModel):
    id: str
    name: str
    service_ids: list[str]
    base_url: str | None
    health_path: str | None
    logs_path: str | None
    metrics_path: str | None
    local_id: str | None = None
    last_status: NormalizedStatus = "unknown"
    health_mapping: CustomHealthMapping | None = None


def registered():
    with database() as db:
        return [
            json.loads(row[0])
            for row in db.execute("SELECT config FROM applications ORDER BY rowid")
        ]


def remember_health(app_id: str, status: str) -> None:
    with database() as db:
        db.execute(
            "UPDATE applications SET config=json_set(config, '$.last_status', ?) WHERE id=?",
            (status, app_id),
        )


async def read_health(app: dict, *, strict_validate: bool = False):
    try:
        result = await external.fetch_external(
            app, "health", strict_validate=strict_validate
        )
    except HTTPException:
        remember_health(app["id"], "unknown")
        raise
    remember_health(app["id"], result.status)
    return result


def get_application(app_id: str):
    if app_id == "demo":
        return {
            "id": "demo",
            "name": "Built-in demo",
            "service_ids": [s.id for s in services.load_services()],
            "base_url": None,
            "health_path": None,
            "logs_path": None,
            "metrics_path": None,
            "health_mapping": None,
        }
    with database() as db:
        row = db.execute(
            "SELECT config FROM applications WHERE id=?", (app_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Application was not found.")
    return json.loads(row[0])


def for_service(service_id: str):
    if not service_id.startswith("ext-"):
        return None
    app = get_application("-".join(service_id.split("-")[:2]))
    if service_id not in app["service_ids"]:
        raise services.ServiceNotFound("Service was not found.")
    return app


@router.get("", response_model=list[Application], dependencies=[Depends(viewer)])
def list_apps():
    return [get_application("demo"), *registered()]


@router.post(
    "/test-connection",
    response_model=external.ConnectionTestResult,
    dependencies=[Depends(operator)],
)
async def test_connection(payload: ConnectionTestRequest):
    return await external.probe_health_connection(
        base_url=payload.base_url,
        service_id=payload.service_id,
        health_path=payload.health_path,
        logs_path=payload.logs_path,
        metrics_path=payload.metrics_path,
        mapping=payload.health_mapping,
    )


@router.post(
    "/validate-mapping",
    response_model=ServiceHealth,
    dependencies=[Depends(operator)],
)
def validate_mapping(payload: MappingPreviewRequest):
    if payload.sample_response is None:
        return ServiceHealth(
            service_id="preview",
            status="unknown",
            http_status=payload.http_status,
            message="Custom response mapping is valid. Test connection with a live or sample response to preview status classification.",
            reason=None,
            requires_mapping=False,
            normalization_method="custom_mapping",
            explanation="Custom mapping passed structural and conflict validation.",
            evidence=None,
        )
    if isinstance(payload.sample_response, str):
        raw_bytes = payload.sample_response.encode("utf-8")
    else:
        raw_bytes = json.dumps(payload.sample_response).encode("utf-8")
    result = normalize_health_response(
        service_id="preview",
        http_status=payload.http_status,
        body=raw_bytes,
        mapping=payload.mapping,
        secret_values=external.configured_secrets(),
    )
    return result


@router.post("", status_code=201, response_model=Application, dependencies=[Depends(operator)])
async def register(payload: Registration):
    origin = external.approved_origin(payload.base_url)
    for path in (payload.health_path, payload.logs_path, payload.metrics_path):
        if path is not None:
            external.safe_path(path)

    try:
        async with asyncio.timeout(5):
            await external.resolve(urlsplit(origin).hostname)
    except (OSError, TimeoutError) as exc:
        raise HTTPException(502, "Unable to resolve the monitoring origin.") from exc
    app_id = "ext-" + uuid.uuid4().hex[:16]
    app = {
        **payload.model_dump(exclude={"service_id"}),
        "base_url": origin,
        "local_id": payload.service_id,
        "id": app_id,
        "service_ids": [app_id + "-" + payload.service_id],
        "last_status": "unknown",
    }
    try:
        with database() as db:
            db.execute(
                "INSERT INTO applications VALUES (?, ?, ?, ?)",
                (app_id, origin, payload.service_id, json.dumps(app)),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This service is already registered.") from exc
    return app


@router.put("/{id}/mapping", response_model=Application, dependencies=[Depends(operator)])
def update_mapping(id: str, payload: MappingUpdateRequest):
    if id == "demo":
        raise HTTPException(409, "Built-in demo services do not use custom mappings.")
    app = get_application(id)
    app["health_mapping"] = (
        payload.health_mapping.model_dump() if payload.health_mapping else None
    )
    app["last_status"] = "unknown"
    with database() as db:
        db.execute(
            "UPDATE applications SET config=? WHERE id=?",
            (json.dumps(app), id),
        )
    return app


@router.post("/{id}/validate", response_model=ServiceHealth, dependencies=[Depends(operator)])
async def validate(id: str):
    if id == "demo":
        raise HTTPException(422, "Select a registered external application.")
    return await read_health(get_application(id), strict_validate=True)


@router.delete("/{id}", status_code=204, dependencies=[Depends(operator)])
def remove(id: str):
    if id == "demo":
        raise HTTPException(409, "The built-in demo is required.")
    with database() as db:
        if not db.execute("DELETE FROM applications WHERE id=?", (id,)).rowcount:
            raise HTTPException(404, "Application was not found.")
