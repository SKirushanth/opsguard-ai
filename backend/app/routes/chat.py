import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from ..models import AgentReply, ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


def get_agent_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.agent_client


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    client: Annotated[httpx.AsyncClient, Depends(get_agent_client)],
) -> ChatResponse:
    return await query_agent(payload, request, client)


async def query_agent(
    payload: ChatRequest, request: Request, client: httpx.AsyncClient
) -> ChatResponse:
    context = {"service_id": payload.service_id} if payload.service_id else {}
    try:
        upstream = await client.post(
            request.app.state.agent_chat_url,
            json={
                "message": payload.message,
                "session_id": payload.session_id,
                "context": context,
            },
            headers={"X-API-Key": os.environ["AGENT_API_KEY"]}
            if os.getenv("AGENT_API_KEY")
            else {},
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

    return ChatResponse(response=reply.response, session_id=payload.session_id)
