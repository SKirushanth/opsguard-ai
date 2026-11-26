import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import access, incidents, registry, scenarios
from .routes.chat import router as chat_router
from .routes.monitoring import router as monitoring_router
from .services import FixtureError, ServiceNotFound

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.agent_chat_url = os.getenv("AGENT_CHAT_URL", "http://127.0.0.1:8000/chat")
    timeout = float(os.getenv("AGENT_CHAT_TIMEOUT_SECONDS", "65"))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
        app.state.agent_client = client
        yield


app = FastAPI(title="OpsGuard AI Monitoring API", version="1.0.0", lifespan=lifespan)
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins if origin.strip()],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(access.router)
app.include_router(scenarios.router)
app.include_router(registry.router)
app.include_router(incidents.router)
app.include_router(monitoring_router, dependencies=[Depends(access.monitor)])
app.include_router(chat_router, dependencies=[Depends(access.viewer)])


@app.exception_handler(RequestValidationError)
async def invalid_request(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [{k: error[k] for k in ("loc", "type", "msg")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(ServiceNotFound)
async def service_not_found(_request: Request, exc: ServiceNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(sqlite3.Error)
async def storage_unavailable(_request: Request, _exc: sqlite3.Error) -> JSONResponse:
    return JSONResponse(
        status_code=503, content={"detail": "Workspace storage is unavailable."}
    )


@app.exception_handler(FixtureError)
async def fixture_unavailable(_request: Request, exc: FixtureError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT") or os.getenv("BACKEND_PORT", "8001")),
    )
