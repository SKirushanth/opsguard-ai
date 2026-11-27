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

def test_demo_analysis_and_incident_lifecycle(monkeypatch):
    from uuid import uuid4
    monkeypatch.setenv("PROTECT_READS", "true")
    with TestClient(app) as c:
        def visitor():
            return {"Authorization": "Bearer " + c.post(BASE + "/session").json()["token"]}
        a, b = visitor(), visitor()
        query = {"service_id": "orders", "session_id": "demo", "message": "investigate"}
        answer = c.post(BASE + "/analysis", json=query, headers=a).json()["response"]
        assert "HTTP 503" in answer
        draft = c.post(BASE + "/incidents/draft", headers=a, json={
            "service_id": "orders", "session_id": "demo", "application_id": "demo", "investigation": answer
        }).json()
        payload = dict(draft, confirmed=True, approval_id=str(uuid4()))
        assert c.post(BASE + "/incidents", headers=a, json=dict(payload,title="unsupported")).status_code == 409
        row = c.post(BASE + "/incidents", headers=a, json=payload).json()
        path = BASE + "/incidents/" + row["id"]
        assert c.patch(path, headers=b, json={"status":"resolved"}).status_code == 404
        assert c.patch(path, headers=a, json={"status":"resolved"}).json()["status"] == "resolved"
        assert c.get(BASE + "/incidents", headers=b).json() == []
        c.post(BASE + "/demo/reset", json={}, headers=a)
        assert c.get(BASE + "/incidents", headers=a).json() == []

def test_hosted_demo_reads_snapshot_and_enforces_quota(monkeypatch):
    import httpx
    import json

    monkeypatch.setenv("PROTECT_READS", "true")
    monkeypatch.setenv("MONITORING_API_KEY", "test-monitor")
    monkeypatch.setenv("AGENT_API_KEY", "test-invoke")
    monkeypatch.setenv("AGENT_CHAT_URL", "https://agent.invalid/chat")
    aliases = []

    async def host(req):
        payload = json.loads(req.content)
        alias = payload["context"]["service_id"]
        aliases.append(alias)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as tools:
            path = f"/api/services/{alias}/health"
            assert (await tools.get(path)).status_code == 401
            result = await tools.get(path, headers={"X-Monitoring-Key":"test-monitor"})
            assert result.json()["service_id"] == alias and result.json()["http_status"] == 401
        return httpx.Response(200,json={"response":json.dumps({"service_id":alias,"resources":["health"],
            "selection":{"observations":["health.http"],"hypotheses":[]}})})

    with TestClient(app) as c:
        monkeypatch.setattr(app.state, "agent_client", httpx.AsyncClient(transport=httpx.MockTransport(host)))
        h = {"Authorization":"Bearer "+c.post(BASE+"/session").json()["token"]}
        scenario = {"service_id":"auth","scenario":"token_validation_failure"}
        query = {"service_id":"auth","session_id":"visitor","message":"Investigate health"}
        for _ in range(3):
            c.post(BASE+"/demo/scenario",headers=h,json=scenario)
            reply=c.post(BASE+"/analysis",headers=h,json=query).json()
            assert reply["mode"]=="live" and "HTTP 401" in reply["response"]
            assert c.get("/api/services/"+aliases[-1]+"/health",headers={"X-Monitoring-Key":"test-monitor"}).status_code==404
            c.post(BASE+"/demo/reset",headers=h,json={})
        assert c.post(BASE+"/analysis",headers=h,json=query).json()["mode"]=="demo"
        app.state.demo_ai.limit=3
        other={"Authorization":"Bearer "+c.post(BASE+"/session").json()["token"]}
        assert c.post(BASE+"/analysis",headers=other,json=query).json()["mode"]=="demo"
        assert len(aliases)==3
