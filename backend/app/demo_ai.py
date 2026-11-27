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
from .routes.chat import investigation_context, investigation_error

router = APIRouter(prefix="/api/services")
logger = logging.getLogger("uvicorn.error").getChild("demo_ai")

QUOTA_REASONS = {"concurrency_limit", "global_daily_limit", "session_limit", "client_daily_limit", "agent_http_429"}
VALIDATION_REASONS = {"wrong_service", "missing_health_read", "unverified_tool_reads",
                      "missing_selection", "unsupported_selection", "invalid_receipt"}

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
    def failure(reason: str):
        logger.info("demo_ai_rejected reason=%s", reason)
        if reason in QUOTA_REASONS:
            return investigation_error("quota", 429, "The AI investigation allowance was reached.")
        if reason in VALIDATION_REASONS:
            return investigation_error("validation", 502, "The AI response could not be verified.")
        if reason in {"agent_timeout", "agent_http_504"}:
            return investigation_error("timeout", 504, "The AI investigation timed out.")
        return investigation_error("unavailable", 503, "The AI investigator is unavailable.")

    key = os.getenv("AGENT_API_KEY")
    url = request.app.state.agent_chat_url
    budget = request.app.state.demo_ai
    if not key:
        raise failure("missing_agent_invocation_key")
    if not url.startswith("https://"):
        raise failure("agent_url_requires_https")
    if not os.getenv("MONITORING_API_KEY"):
        raise failure("missing_monitoring_key")
    # Use the transport peer, never a visitor-supplied forwarding header.
    limit_reason = budget.reserve(session, request.client.host if request.client else "unknown")
    if limit_reason:
        raise failure(limit_reason)
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
            raise failure("wrong_service")
        if "health" not in reads:
            raise failure("missing_health_read")
        if not set(receipt.resources).issubset(reads):
            raise failure("unverified_tool_reads")
        verified = Evidence.collect(payload.service_id, {r: observations[r] for r in receipt.resources}, [])
        if not receipt.selection:
            raise failure("missing_selection")
        if not verified.accepts(receipt.selection):
            raise failure("unsupported_selection")
        content = ev.conversation(receipt.selection, payload.message) if payload.conversational else {
            "response": ev.render(receipt.selection),
        }
        return {"session_id": payload.session_id, "mode": "live", **content}
    except httpx.HTTPStatusError as exc:
        raise failure(f"agent_http_{exc.response.status_code}") from exc
    except (httpx.TimeoutException, TimeoutError):
        raise failure("agent_timeout") from None
    except httpx.HTTPError:
        raise failure("agent_transport_error") from None
    except (ValidationError, ValueError, KeyError, TypeError):
        raise failure("invalid_receipt") from None
    finally:
        budget.active.pop(alias, None)
