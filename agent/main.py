import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from pydantic import BaseModel, Field, StringConstraints

if not __package__:
    # Support running this file directly from the agent directory.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "agent"

from investigation import REFERENCE_FORMAT, Investigation

from .config import load_settings
from .graph import InvalidModelResponse, build_graph
from .tools import build_monitoring_tools

logger = logging.getLogger("uvicorn.error").getChild("opsguard.agent")


class ChatRequest(BaseModel):
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    context: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    response: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    app.state.investigation_graph = None
    async with httpx.AsyncClient(
        base_url=settings.monitoring_url,
        timeout=settings.monitoring_timeout,
        headers={"X-Monitoring-Key": os.environ["MONITORING_API_KEY"]}
        if os.getenv("MONITORING_API_KEY")
        else {},
    ) as client:
        if settings.llm_api_key:
            model = ChatOpenAI(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout,
                max_retries=0,
                use_responses_api=False,
            )
            app.state.investigation_graph = build_graph(
                model, build_monitoring_tools(client)
            )
        yield


app = FastAPI(title="OpsGuard AI Agent", version="1.0.0", lifespan=lifespan)


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    graph = request.app.state.investigation_graph
    if graph is None:
        raise HTTPException(status_code=503, detail="The agent requires LLM_API_KEY.")

    service_id = payload.context.get("service_id")
    message = payload.message
    if service_id is not None:
        if not isinstance(service_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9-]{0,63}", service_id
        ):
            raise HTTPException(422, "Invalid service context.")
        message = f"Selected monitoring service ID: {service_id}\n{message}"
    try:
        async with asyncio.timeout(60):
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config={
                    "recursion_limit": 12,
                    "configurable": {"service_id": service_id, "draft": payload.message.startswith("Prepare an incident draft.")},
                },
            )
        answer = result["messages"][-1].text.strip()
        if not answer:
            raise InvalidModelResponse
    except (TimeoutError, APITimeoutError) as exc:
        raise HTTPException(
            status_code=504, detail="The investigation timed out."
        ) from exc
    except (AuthenticationError, PermissionDeniedError, RateLimitError) as exc:
        logger.warning("model_unavailable error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="The language model is unavailable. Check provider access and quota.",
        ) from exc
    except (BadRequestError, NotFoundError, UnprocessableEntityError) as exc:
        logger.warning("model_request_rejected error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="The provider rejected the request. Check the model, base URL, and support for tool calling.",
        ) from exc
    except (
        APIConnectionError,
        APIResponseValidationError,
        APIStatusError,
        GraphRecursionError,
        InvalidModelResponse,
    ) as exc:
        logger.warning("investigation_failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="The agent could not complete the model investigation.",
        ) from exc

    receipt = result["messages"][-1].additional_kwargs.get("investigation")
    # Use the existing WSO2 chat envelope for both human replies and gateway references.
    if receipt and payload.context.get("response_format") == REFERENCE_FORMAT:
        answer = Investigation.model_validate(receipt).model_dump_json()
    return ChatResponse(response=answer)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=load_settings().port)
