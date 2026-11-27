from fastapi.testclient import TestClient

from backend.app import public_demo
from backend.app.main import app

BASE = "/api/public-demo"


def test_isolated_visitors(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "true")
    monkeypatch.setenv("OPERATOR_PASSWORD", "test-only")
    with TestClient(app) as client:
        def headers(path, body=None):
            response = client.post(path, json=body)
            assert response.status_code == 200
            return {"Authorization": "Bearer " + response.json()["token"]}

        a, b = headers(BASE + "/session"), headers(BASE + "/session")
        operator = headers("/api/session", {"password": "test-only"})
        path = "/services/auth/health"
        assert client.get(BASE + path).status_code == 401
        assert client.get(BASE + path, headers=operator).status_code == 401
        for endpoint in [path, "/services", "/applications", "/incidents"]:
            assert client.get("/api" + endpoint, headers=a).status_code == 401
        assert client.post("/api/chat", headers=a, json={"message":"test","session_id":"test"}).status_code == 401
        assert client.post(BASE + "/chat", headers=a).status_code == 404
        assert client.get(BASE + "/services/ext-test/health", headers=a).status_code == 422
        scenario = {"service_id":"auth", "scenario":"token_validation_failure"}
        assert client.post("/api/demo/scenario", headers=a, json=scenario).status_code == 401
        assert client.post(BASE + "/demo/scenario", headers=a, json=scenario).status_code == 200
        assert client.get(BASE + path, headers=a).json()["status"] == "down"
        assert client.get(BASE + path, headers=b).json()["status"] == "healthy"
        assert client.get("/api" + path, headers=operator).json()["status"] == "healthy"
        assert client.get(BASE + "/services/auth/metrics", headers=a).json()["metrics"]["error_rate_percent"] == 38
        assert client.get(BASE + "/services/auth/logs", headers=a).json()["logs"][0]["level"] == "ERROR"
        assert client.post(BASE + "/demo/reset", headers=a, json={}).status_code == 200
        assert client.get(BASE + path, headers=a).json()["status"] == "healthy"
        assert client.get(BASE + "/services/orders/health", headers=b).json()["status"] == "down"
        monkeypatch.setattr(public_demo, "MAX_SESSIONS", 2)
        assert client.post(BASE + "/session").status_code == 429


def test_expiration(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "true")
    monkeypatch.setattr(public_demo, "TTL", 0)
    with TestClient(app) as client:
        token = client.post(BASE + "/session").json()["token"]
        assert client.get(BASE + "/services", headers={"Authorization": "Bearer " + token}).status_code == 401
        assert not app.state.demo_sessions
