"""Short-lived evidence snapshots for the hosted agent's existing read-only tools."""
import asyncio
import logging
import os
import secrets
from collections import deque
from time import monotonic
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from investigation import Evidence, Investigation
from .access import matches
from .routes.chat import investigation_context

router = APIRouter(prefix="/api/services")
logger = logging.getLogger("uvicorn.error").getChild("demo_ai")


class Budget:
    def __init__(self):
        self.active = {}
        self.calls = deque()
        self.limit = max(0, int(os.getenv("DEMO_AI_DAILY_LIMIT", "30")))

    def reserve(self, session, client):
        now = monotonic()
        while self.calls and now - self.calls[0][0] >= 86400:
            self.calls.popleft()
        if len(self.active) >= 2:
            return "concurrency_limit"
        if len(self.calls) >= self.limit:
            return "global_daily_limit" if self.limit else "live_ai_disabled"
        if session.get("ai_calls", 0) >= 3:
            return "session_limit"
        if sum(ip == client for _, ip in self.calls) >= 9:
            return "client_daily_limit"
        self.calls.append((now, client))
        session["ai_calls"] = session.get("ai_calls", 0) + 1
        return None


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
    def reply_for(mode, selection=None):
        content = ev.conversation(selection, payload.message) if payload.conversational else {"response": ev.render(selection)}
        return {"session_id": payload.session_id, "mode": mode, **content}

    def fallback(reason: str):
        logger.info("demo_ai_fallback reason=%s", reason)
        return reply_for("demo")

    key = os.getenv("AGENT_API_KEY")
    url = request.app.state.agent_chat_url
    budget = request.app.state.demo_ai
    if not key:
        return fallback("missing_agent_invocation_key")
    if not url.startswith("https://"):
        return fallback("agent_url_requires_https")
    if not os.getenv("MONITORING_API_KEY"):
        return fallback("missing_monitoring_key")
    # Use the transport peer, never a visitor-supplied forwarding header.
    limit_reason = budget.reserve(session, request.client.host if request.client else "unknown")
    if limit_reason:
        return fallback(limit_reason)
    alias = "demo-ai-" + secrets.token_hex(12)
    reads = []
    budget.active[alias] = observations, reads
    try:
        async with asyncio.timeout(60):
            reply = await request.app.state.agent_client.post(url, headers={"X-API-Key":key}, json={
                "message":payload.message, "session_id":secrets.token_hex(16),
                "context":investigation_context(payload, alias),
            })
        reply.raise_for_status()
        receipt = Investigation.model_validate_json(reply.json()["response"])
        if receipt.service_id != alias:
            return fallback("wrong_service")
        if "health" not in reads:
            return fallback("missing_health_read")
        if not set(receipt.resources).issubset(reads):
            return fallback("unverified_tool_reads")
        verified = Evidence.collect(payload.service_id, {r: observations[r] for r in receipt.resources}, [])
        if not receipt.selection:
            return fallback("missing_selection")
        if not verified.accepts(receipt.selection):
            return fallback("unsupported_selection")
        return reply_for("live", receipt.selection)
    except httpx.HTTPStatusError as exc:
        return fallback(f"agent_http_{exc.response.status_code}")
    except (httpx.TimeoutException, TimeoutError):
        return fallback("agent_timeout")
    except httpx.HTTPError:
        return fallback("agent_transport_error")
    except (ValidationError, ValueError, KeyError, TypeError):
        return fallback("invalid_receipt")
    finally:
        budget.active.pop(alias, None)
