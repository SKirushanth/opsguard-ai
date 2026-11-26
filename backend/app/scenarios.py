from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .access import operator
from .database import database
from .models import LogEntry, Metrics
from .services import ServiceRecord, get_service, load_services

router = APIRouter(
    prefix="/api/demo", tags=["simulation"], dependencies=[Depends(operator)]
)
DemoID = Literal["auth", "orders", "notifications"]
FAILURES = {
    "auth": (
        "token_validation_failure",
        "Token validation failed for authentication requests",
        401,
        38,
        180,
    ),
    "orders": (
        "database_pool_exhausted",
        "Database connection pool exhausted; database connection timeout",
        503,
        42,
        2400,
    ),
    "notifications": (
        "smtp_timeout",
        "SMTP connection timeout during email delivery",
        504,
        24,
        3200,
    ),
}


class Scenario(BaseModel):
    service_id: DemoID
    scenario: str


class Reset(BaseModel):
    service_id: DemoID | None = None


def state(service_id: str):
    with database() as db:
        return db.execute(
            "SELECT * FROM scenarios WHERE service_id=?", (service_id,)
        ).fetchone()


def snapshot(original: ServiceRecord, row):
    healthy = row["scenario"] == "healthy"
    _, failure, code, errors, latency = FAILURES[original.id]
    message = (
        f"{original.name} completed its health check successfully"
        if healthy
        else failure
    )
    status = (
        "healthy"
        if healthy
        else "degraded"
        if original.id == "notifications"
        else "down"
    )
    record = original.model_copy(
        update={"status": status, "http_status": 200 if healthy else code, "message": message}
    )
    logs = [
        LogEntry(
            timestamp=datetime.fromisoformat(row["changed_at"]),
            level="INFO" if healthy else "ERROR",
            message=f"HTTP {record.http_status} · {message}",
        )
    ]
    metrics = Metrics(
        error_rate_percent=0 if healthy else errors,
        avg_latency_ms=65 if healthy else latency,
        requests_last_5m=120,
        active_db_connections=(8 if healthy else 100)
        if original.id == "orders"
        else None,
        db_connection_limit=100 if original.id == "orders" else None,
    )
    return record, logs, metrics


def apply_scenarios(items: dict[str, str]):
    changed_at = datetime.now(UTC).isoformat()
    with database() as db:
        db.executemany(
            "INSERT OR REPLACE INTO scenarios VALUES (?, ?, ?)",
            [(service, scenario, changed_at) for service, scenario in items.items()],
        )


@router.post("/scenario")
def activate(payload: Scenario):
    if payload.scenario != FAILURES[payload.service_id][0]:
        raise HTTPException(422, "Unsupported scenario for this demo service.")
    original = get_service(payload.service_id)
    apply_scenarios({original.id: payload.scenario})
    status = "degraded" if original.id == "notifications" else "down"
    return {**payload.model_dump(), "status": status}


@router.post("/reset")
def reset(payload: Reset):
    ids = (
        [payload.service_id] if payload.service_id else [s.id for s in load_services()]
    )
    apply_scenarios(dict.fromkeys(ids, "healthy"))
    return {"service_ids": ids, "status": "healthy"}
