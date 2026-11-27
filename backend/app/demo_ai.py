"""Short-lived evidence snapshots for the hosted agent's existing read-only tools."""
import asyncio
import os
import secrets
from collections import deque
from time import monotonic
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from investigation import Evidence, Investigation, REFERENCE_FORMAT
from .access import matches

router = APIRouter(prefix="/api/services")


class Budget:
    def __init__(self):
        self.active = {}
        self.calls = deque()
        self.limit = max(0, int(os.getenv("DEMO_AI_DAILY_LIMIT", "30")))

    def reserve(self, session, client):
        now = monotonic()
        while self.calls and now - self.calls[0][0] >= 86400:
            self.calls.popleft()
        if (len(self.active) >= 2 or len(self.calls) >= self.limit or
                session.get("ai_calls", 0) >= 3 or sum(ip == client for _, ip in self.calls) >= 9):
            return False
        self.calls.append((now, client))
        session["ai_calls"] = session.get("ai_calls", 0) + 1
        return True


@router.get("/demo-ai-{snapshot_id}/{resource}")
async def tool_read(snapshot_id: str, resource: Literal["health", "logs", "metrics"], request: Request, limit: int = 20):
    if not matches(request.headers.get("X-Monitoring-Key", ""), os.getenv("MONITORING_API_KEY", "")):
        raise HTTPException(401)
    alias = "demo-ai-" + snapshot_id
    item = request.app.state.demo_ai.active.get(alias)
    if not item:
        raise HTTPException(404)
    observations, reads = item
    if len(reads) >= 9:
        raise HTTPException(429)
    reads.append(resource)
    data = dict(observations[resource], service_id=alias)
    if resource == "logs":
        data["logs"] = data["logs"][:max(1, min(limit, 100))]
    return data


async def investigate(request, payload, session, observations):
    ev = Evidence.collect(payload.service_id, observations, [])
    fallback = {"session_id": payload.session_id, "mode":"demo", "response":ev.render(None)}
    key = os.getenv("AGENT_API_KEY")
    url = request.app.state.agent_chat_url
    budget = request.app.state.demo_ai
    if not (key and url.startswith("https://") and os.getenv("MONITORING_API_KEY")):
        return fallback
    # Use the transport peer, never a visitor-supplied forwarding header.
    if not budget.reserve(session, request.client.host if request.client else "unknown"):
        return fallback
    alias = "demo-ai-" + secrets.token_hex(12)
    reads = []
    budget.active[alias] = observations, reads
    try:
        async with asyncio.timeout(60):
            reply = await request.app.state.agent_client.post(url, headers={"X-API-Key":key}, json={
                "message":payload.message, "session_id":secrets.token_hex(16),
                "context":{"service_id":alias, "response_format":REFERENCE_FORMAT},
            })
        reply.raise_for_status()
        receipt = Investigation.model_validate_json(reply.json()["response"])
        if receipt.service_id != alias or not set(receipt.resources).issubset(reads):
            return fallback
        verified = Evidence.collect(payload.service_id, {r: observations[r] for r in receipt.resources}, [])
        if not receipt.selection or not verified.accepts(receipt.selection) or "health" not in reads:
            return fallback
        return {"session_id":payload.session_id, "mode":"live", "response":ev.render(receipt.selection)}
    except (httpx.HTTPError, TimeoutError, ValidationError, ValueError, KeyError, TypeError):
        return fallback
    finally:
        budget.active.pop(alias, None)
