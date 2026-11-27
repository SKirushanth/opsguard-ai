import hashlib
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr

from .database import database

router = APIRouter(prefix="/api", tags=["operator"])


class Login(BaseModel):
    password: SecretStr


def matches(value: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(value.encode(), expected.encode())


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def operator(request: Request) -> None:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token:
        with database() as db:
            if db.execute(
                "SELECT 1 FROM sessions WHERE digest=? AND expires>?",
                (digest(token), int(time.time())),
            ).fetchone():
                return
    raise HTTPException(401, "Sign in as the workspace operator.")


def viewer(request: Request) -> None:
    if os.getenv("PROTECT_READS", "true").strip().lower() != "false":
        operator(request)


def monitor(request: Request) -> None:
    if matches(
        request.headers.get("X-Monitoring-Key", ""), os.getenv("MONITORING_API_KEY", "")
    ):
        return
    viewer(request)


@router.post("/session")
def login(payload: Login, response: Response):
    password = os.getenv("OPERATOR_PASSWORD", "")
    if not password:
        raise HTTPException(503, "Operator sign-in requires server configuration.")
    if not matches(payload.password.get_secret_value(), password):
        raise HTTPException(401, "Invalid operator credentials.")
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with database() as db:
        db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?)", (digest(token), now + 3600))
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "expires_in": 3600}


@router.delete("/session", status_code=204)
def logout(request: Request):
    operator(request)
    with database() as db:
        db.execute(
            "DELETE FROM sessions WHERE digest=?",
            (digest(request.headers["Authorization"].split(" ")[1]),),
        )
