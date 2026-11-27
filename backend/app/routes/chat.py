import asyncio
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from investigation import CLARIFICATION, REFERENCE_FORMAT, Evidence, Investigation

from ..models import AgentReply, ChatRequest, ChatResponse
from ..services import FixtureError, ServiceNotFound
from . import monitoring

router = APIRouter(prefix="/api", tags=["chat"])


def get_agent_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.agent_client


async def verified_response(receipt: Investigation, *, draft: bool) -> str:
    # The configured agent supplies only references. Resolve them against the backend
    # before displaying a claim, even if the agent deployment returned arbitrary prose.
    readers = {
        "health": monitoring.service_health,
        "logs": monitoring.service_logs,
        "metrics": monitoring.service_metrics,
    }
    resources = list(dict.fromkeys(["health", *receipt.resources]))

    async def read(resource):
        try:
            result = await readers[resource](receipt.service_id)
            return result.model_dump(mode="json")
        except (HTTPException, FixtureError, ServiceNotFound):
            return None

    results = await asyncio.gather(*(read(resource) for resource in resources))
    observations = {k: v for k, v in zip(resources, results) if v is not None}
    errors = [f"{k.capitalize()} monitoring is unavailable for this service." for k in resources if k not in observations]
    evidence = Evidence.collect(receipt.service_id, observations, errors)
    return evidence.render(receipt.selection, draft=draft)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    client: Annotated[httpx.AsyncClient, Depends(get_agent_client)],
) -> ChatResponse:
    return await query_agent(payload, request, client)


async def query_agent(
    payload: ChatRequest, request: Request, client: httpx.AsyncClient, *, draft: bool = False
) -> ChatResponse:
    context = {"response_format": REFERENCE_FORMAT}
    if payload.service_id:
        context["service_id"] = payload.service_id
    try:
        upstream = await client.post(
            request.app.state.agent_chat_url,
            json={
                "message": payload.message,
                "session_id": payload.session_id,
                "context": context,
            },
            headers={"X-API-Key": os.environ["AGENT_API_KEY"]} if os.getenv("AGENT_API_KEY") else {},
        )
        upstream.raise_for_status()
        reply = AgentReply.model_validate_json(upstream.content)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504, detail="The agent request timed out."
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503, detail="The agent is unreachable."
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 504:
            raise HTTPException(
                status_code=504, detail="The agent investigation timed out."
            ) from exc
        if status == 503:
            raise HTTPException(
                status_code=503,
                detail="The agent is unavailable. Check its service and LLM configuration.",
            ) from exc
        raise HTTPException(
            status_code=502, detail="The agent could not complete the request."
        ) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=502, detail="The agent returned an invalid response."
        ) from exc

    if payload.service_id is None and reply.response == CLARIFICATION:
        return ChatResponse(response=CLARIFICATION, session_id=payload.session_id)
    try:
        receipt = Investigation.model_validate_json(reply.response)
    except ValidationError as exc:
        raise HTTPException(502, "The agent returned no verifiable monitoring references.") from exc
    if payload.service_id and receipt.service_id != payload.service_id:
        raise HTTPException(502, "The agent returned evidence for a different service.")
    answer = await verified_response(receipt, draft=draft)
    return ChatResponse(response=answer, session_id=payload.session_id)
