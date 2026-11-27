"""Visitor-only routes. Each temporary session owns at most three scenario overrides."""
import asyncio
import os
import secrets
from datetime import UTC, datetime
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import registry, scenarios, services
from .access import digest
from .models import ServiceHealth

router = APIRouter(prefix="/api/public-demo", tags=["public demo"])
TTL = 1800
MAX_SESSIONS = 1024


def sessions(request: Request):
    # All accesses run synchronously on the ASGI event loop between awaits.
    return request.app.state.demo_sessions


@router.post("/session")
async def start(request: Request, response: Response):
    if os.getenv("PROTECT_READS", "false").strip().lower() == "false":
        raise HTTPException(403, "Enable protected workspace reads to use public demo sessions.")
    store = sessions(request)
    if len(store) >= MAX_SESSIONS:
        raise HTTPException(429, "Demo capacity reached. Try again later.")
    token = secrets.token_urlsafe(32)
    key = digest(token)
    store[key] = (monotonic() + TTL, {})
    asyncio.get_running_loop().call_later(TTL, store.pop, key, None)
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "expires_in": TTL}


async def visitor(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    entry = sessions(request).get(digest(token))
    if scheme.lower() != "bearer" or not entry or entry[0] <= monotonic():
        raise HTTPException(401, "Your demo session has ended.")
    return entry[1]


@router.get("/applications", dependencies=[Depends(visitor)])
async def applications():
    return [registry.get_application("demo")]


def snapshot(service_id, state):
    original = services.get_service(service_id)
    if service_id in state:
        return scenarios.snapshot(original, state[service_id])
    return original, services.get_logs(service_id, 100), services.get_metrics(service_id)


@router.get("/services")
async def catalog(state=Depends(visitor)):
    return {"services": [snapshot(id, state)[0] for id in scenarios.FAILURES]}


@router.get("/services/{id}/{resource}")
async def read(id: scenarios.DemoID, resource: str, state=Depends(visitor)):
    record, logs, metrics = snapshot(id, state)
    if resource == "health":
        return ServiceHealth(service_id=id, status=record.status, http_status=record.http_status,
                             message=record.message, explanation=record.message,
                             normalization_method="simulated", last_checked_at=datetime.now(UTC))
    if resource == "logs":
        return {"service_id": id, "logs": logs}
    if resource == "metrics":
        return {"service_id": id, "metrics": metrics}
    raise HTTPException(404)


def change(state, id, scenario):
    state[id] = {"scenario": scenario, "changed_at": datetime.now(UTC).isoformat()}


@router.post("/demo/scenario")
async def activate(payload: scenarios.Scenario, state=Depends(visitor)):
    if payload.scenario != scenarios.FAILURES[payload.service_id][0]:
        raise HTTPException(422, "Unsupported scenario for this demo service.")
    change(state, payload.service_id, payload.scenario)
    return {**payload.model_dump(), "status": snapshot(payload.service_id, state)[0].status}


@router.post("/demo/reset")
async def reset(payload: scenarios.Reset, state=Depends(visitor)):
    ids = [payload.service_id] if payload.service_id else list(scenarios.FAILURES)
    for id in ids:
        change(state, id, "healthy")
    return {"service_ids": ids, "status": "healthy"}
