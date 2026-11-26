import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import external
from backend.app.main import app
from backend.app.routes.chat import get_agent_client


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPERATOR_PASSWORD", "workflow-test-password")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth(client):
    login = client.post("/api/session", json={"password": "workflow-test-password"})
    return {"Authorization": "Bearer " + login.json()["token"]}


@pytest.mark.parametrize(
    "service,scenario,status,needle",
    [
        ("auth", "token_validation_failure", "down", "Token validation"),
        ("orders", "database_pool_exhausted", "down", "pool exhausted"),
        ("notifications", "smtp_timeout", "degraded", "SMTP"),
    ],
)
def test_demo_cycle(client, auth, service, scenario, status, needle):
    assert client.post("/api/demo/reset", headers=auth, json={}).status_code == 200
    assert all(
        s["status"] == "healthy" for s in client.get("/api/services").json()["services"]
    )
    data = {"service_id": service, "scenario": scenario}
    assert client.post("/api/demo/scenario", json=data).status_code == 401
    assert client.post("/api/demo/scenario", headers=auth, json=data).json() == {
        **data,
        "status": status,
    }
    url = f"/api/services/{service}/"
    assert client.get(url + "health").json()["status"] == status
    assert needle in client.get(url + "logs").json()["logs"][0]["message"]
    metrics = client.get(url + "metrics").json()["metrics"]
    assert metrics["error_rate_percent"] > 10
    if service == "orders":
        assert metrics["active_db_connections"] == metrics["db_connection_limit"] == 100
    assert (
        client.post(
            "/api/demo/reset", headers=auth, json={"service_id": service}
        ).status_code
        == 200
    )
    assert client.get(url + "health").json()["http_status"] == 200
    assert client.get(url + "logs").json()["logs"][0]["level"] == "INFO"
    assert client.get(url + "metrics").json()["metrics"]["error_rate_percent"] == 0
    assert (
        client.post(
            "/api/demo/scenario", headers=auth, json={**data, "scenario": "invalid"}
        ).status_code
        == 422
    )


def test_session_boundaries(client, auth, monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "true")
    monkeypatch.setenv("MONITORING_API_KEY", "read-only-test")
    reader = {"X-Monitoring-Key": "read-only-test"}
    assert client.get("/api/services").status_code == 401
    assert client.get("/api/services", headers=reader).is_success
    assert client.post("/api/demo/reset", json={}, headers=reader).status_code == 401
    assert (
        client.post(
            "/api/chat", json={"message": "Hi", "session_id": "abc"}, headers=reader
        ).status_code
        == 401
    )
    assert client.get("/api/applications", headers=auth).is_success
    assert client.delete("/api/session", headers=auth).status_code == 204
    assert client.get("/api/services", headers=auth).status_code == 401
    assert client.post("/api/session", json={"password": "wrong"}).status_code == 401


REPORT = {
    "title": "Order failures",
    "description": "Requests fail.",
    "severity": "critical",
    "evidence": ["HTTP 503"],
    "likely_root_cause": "Pool exhaustion",
    "recommendations": ["Inspect connections"],
}


def test_confirm_and_retry(client, auth):
    body = {
        **REPORT,
        "application_id": "demo",
        "service_id": "orders",
        "approval_id": str(uuid4()),
    }
    assert client.post("/api/incidents", headers=auth, json=body).status_code == 422
    for value in (False, "true", 1):
        assert (
            client.post(
                "/api/incidents", headers=auth, json={**body, "confirmed": value}
            ).status_code
            == 422
        )
    body["confirmed"] = True
    assert client.post("/api/incidents", json=body).status_code == 401
    assert client.get("/api/incidents").json() == []
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(
            pool.map(
                lambda _: client.post("/api/incidents", headers=auth, json=body),
                range(2),
            )
        )
    assert sorted(r.status_code for r in result) == [200, 201]
    row = result[0].json()
    assert client.get("/api/incidents").json() == [row]
    assert (
        client.post(
            "/api/incidents", headers=auth, json={**body, "title": "Different"}
        ).status_code
        == 409
    )
    path = "/api/incidents/" + row["id"]
    assert client.patch(path, json={"status": "resolved"}).status_code == 401
    assert client.patch(path, headers=auth, json={"status": "resolved"}).json()[
        "resolved_at"
    ]
    assert (
        client.patch(path, headers=auth, json={"status": "open"}).json()["resolved_at"]
        is None
    )


@pytest.mark.parametrize("valid", [True, False])
def test_draft_is_read_only_and_proxy_uses_cloud_key(client, auth, valid, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "gateway-test")

    def respond(request):
        assert request.headers["X-API-Key"] == "gateway-test"
        payload = json.loads(request.content)
        assert payload["context"] == {"service_id": "orders"}
        assert "gateway-test" not in request.content.decode()
        return httpx.Response(
            200, json={"response": json.dumps(REPORT) if valid else "No evidence."}
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setitem(app.dependency_overrides, get_agent_client, lambda: upstream)
    result = client.post(
        "/api/incidents/draft",
        headers=auth,
        json={
            "application_id": "demo",
            "service_id": "orders",
            "session_id": "example",
            "investigation": "Orders returned errors.",
        },
    )
    assert result.status_code == (200 if valid else 502)
    if valid:
        assert result.json() == {
            **REPORT,
            "application_id": "demo",
            "service_id": "orders",
        }
    assert client.get("/api/incidents").json() == []


REG = {
    "name": "Library",
    "base_url": "https://monitor.example.com",
    "service_id": "library",
    "health_path": "/health",
}


@pytest.fixture
def approved(monkeypatch):
    monkeypatch.setenv("APPROVED_HTTPS_ORIGINS", REG["base_url"])

    async def resolve(_host):
        return "93.184.216.34"

    monkeypatch.setattr(external, "resolve", resolve)


def test_registration(client, auth, approved, monkeypatch):
    assert client.post("/api/applications", json=REG).status_code == 401
    reg = client.post("/api/applications", json=REG, headers=auth)
    assert reg.status_code == 201
    item = reg.json()
    assert client.post("/api/applications", json=REG, headers=auth).status_code == 409
    sid = item["service_ids"][0]
    assert sid in [s["id"] for s in client.get("/api/services").json()["services"]]
    assert client.get(f"/api/services/{sid}/logs").status_code == 501
    assert client.get(f"/api/services/{sid}/metrics").status_code == 501
    assert (
        client.post(
            "/api/demo/reset", json={"service_id": sid}, headers=auth
        ).status_code
        == 422
    )
    monkeypatch.setenv(
        "EXTERNAL_BEARER_TOKENS",
        json.dumps({REG["base_url"] + "/health": "origin-only-token"}),
    )

    def respond(request):
        assert request.url.host == "93.184.216.34"
        assert (
            request.headers["Host"]
            == request.extensions["sni_hostname"]
            == "monitor.example.com"
        )
        assert request.headers["Authorization"] == "Bearer origin-only-token"
        payload = {
            "service_id": "library",
            "status": "degraded",
            "http_status": 503,
            "message": "Queue busy",
        }
        return httpx.Response(
            200, stream=httpx.ByteStream(json.dumps(payload).encode())
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        external.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    for result in [
        client.post("/api/applications/" + item["id"] + "/validate", headers=auth),
        client.get(f"/api/services/{sid}/health"),
    ]:
        assert (
            result.json()["service_id"] == sid and result.json()["status"] == "degraded"
        )
        assert "origin-only-token" not in result.text
    assert client.delete("/api/applications/demo", headers=auth).status_code == 409
    assert (
        client.delete("/api/applications/" + item["id"], headers=auth).status_code
        == 204
    )
    assert client.get(f"/api/services/{sid}/health").status_code == 404


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_url", "http://monitor.example.com"),
        ("base_url", "https://localhost"),
        ("base_url", "https://169.254.169.254"),
        ("base_url", "https://127.0.0.1"),
        ("base_url", "https://user@monitor.example.com"),
        ("base_url", "https://unapproved.example.com"),
        ("health_path", "//other.example.com"),
        ("logs_path", "/../private"),
        ("metrics_path", "/%2e%2e"),
    ],
)
def test_invalid_registration(client, auth, approved, field, value):
    result = client.post("/api/applications", json={**REG, field: value}, headers=auth)
    assert result.status_code in (403, 422)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "address",
    ["10.0.0.1", "127.0.0.1", "169.254.169.254", "224.0.0.1", "::1", "192.0.2.1"],
)
async def test_restricted_dns_answers(address, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    async def answers(*args, **kwargs):
        return [
            (None, None, None, None, (ip, 443)) for ip in ["93.184.216.34", address]
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", answers)
    with pytest.raises(HTTPException) as exc:
        await external.resolve("monitor.example.com")
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "code,body",
    [(302, b""), (500, b"private failure details"), (200, b"{}"), (200, b"x" * 65537)],
)
def test_external_errors(client, auth, approved, monkeypatch, code, body):
    item = client.post("/api/applications", json=REG, headers=auth).json()
    original = httpx.AsyncClient

    def respond(request):
        assert request.url.host == "93.184.216.34"
        return httpx.Response(
            code,
            headers={"Location": "https://elsewhere.example.com"},
            stream=httpx.ByteStream(body),
        )

    monkeypatch.setattr(
        external.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    result = client.post("/api/applications/" + item["id"] + "/validate", headers=auth)
    assert result.status_code == 502
    assert "private failure details" not in result.text


def test_simple_health_endpoint():
    result = external.parse_health(b'{"status":"ok","db":"connected","items":3}', "cafe-api")
    assert result.service_id == "cafe-api"
    assert result.status == "healthy"
    assert result.http_status == 200
