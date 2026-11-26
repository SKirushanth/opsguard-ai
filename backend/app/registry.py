import asyncio
import json
import sqlite3
import uuid
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import external, services
from .access import operator, viewer
from .database import database
from .models import ServiceHealth, ServiceStatus

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


class Application(BaseModel):
    id: str
    name: str
    service_ids: list[str]
    base_url: str | None
    health_path: str | None
    logs_path: str | None
    metrics_path: str | None
    local_id: str | None = None
    last_status: ServiceStatus | Literal["unknown"] = "unknown"


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


async def read_health(app: dict):
    try:
        result = await external.fetch_external(app, "health")
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


@router.post("/{id}/validate", response_model=ServiceHealth, dependencies=[Depends(operator)])
async def validate(id: str):
    if id == "demo":
        raise HTTPException(422, "Select a registered external application.")
    return await read_health(get_application(id))


@router.delete("/{id}", status_code=204, dependencies=[Depends(operator)])
def remove(id: str):
    if id == "demo":
        raise HTTPException(409, "The built-in demo is required.")
    with database() as db:
        if not db.execute("DELETE FROM applications WHERE id=?", (id,)).rowcount:
            raise HTTPException(404, "Application was not found.")
