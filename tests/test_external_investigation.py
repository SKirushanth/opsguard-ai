import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from test_chat import ScriptedModel

from agent.graph import build_graph
from agent.tools import build_monitoring_tools
from backend.app import external
from backend.app.main import app


@pytest.mark.anyio
async def test_registered_service_with_partial_evidence(monkeypatch):
    """Real registry and graph, with only the remote origin and model substituted."""
    monkeypatch.setenv("APPROVED_HTTPS_ORIGINS", "https://monitor.example.com")
    monkeypatch.setenv("OPERATOR_PASSWORD", "test-operator")
    async def resolve(host):
        assert host == "monitor.example.com"
        return "93.184.216.34"

    seen = []
    def origin(request):
        seen.append(request.url.path)
        body = {"service_id": "library"}
        if request.url.path == "/ready":
            body.update(status="degraded", http_status=503, message="Queue delayed")
        else:
            assert request.url.path == "/events"
            body["logs"] = [{"timestamp": "2026-11-24T10:00:00Z", "level":"WARNING", "message":"Queue age high"}]
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    monkeypatch.setattr(external, "resolve", resolve)
    original = httpx.AsyncClient
    async with original(transport=httpx.ASGITransport(app=app), base_url="http://backend") as gateway:
        monkeypatch.setattr(external.httpx, "AsyncClient", lambda **kw:original(transport=httpx.MockTransport(origin), **kw))
        token = (await gateway.post("/api/session", json={"password":"test-operator"})).json()["token"]
        result = await gateway.post("/api/applications", headers={"Authorization":"Bearer "+token}, json={
            "base_url":"https://monitor.example.com", "service_id":"library", "health_path":"/ready",
            "logs_path":"/events", "name":"Library"})
        assert result.status_code == 201
        sid = result.json()["service_ids"][0]

        def reply(messages):
            evidence = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
            if not evidence:
                return AIMessage(content="", tool_calls=[{
                    "name":"get_service_"+name,"id":name,"args":{"service":sid}
                } for name in ("health","logs","metrics")])
            assert json.loads(evidence["health"].content)["service_id"] == sid
            assert json.loads(evidence["logs"].content)["logs"][0]["message"] == "Queue age high"
            assert evidence["metrics"].status == "error"
            return AIMessage(content="External monitoring reports delayed queues. " + evidence["metrics"].text)

        graph = build_graph(ScriptedModel(reply=reply), build_monitoring_tools(gateway))
        result = await graph.ainvoke({"messages":[HumanMessage(content="Investigate the selected service")]},
                                     config={"configurable":{"service_id":sid}})
        answer = result["messages"][-1].text
        assert answer.startswith(f"Monitored health for {sid}: degraded.")
        assert "Queue age high" in answer and "Metrics monitoring is unavailable for this service." in answer
        assert "External monitoring reports delayed queues." not in answer  # unstructured model prose
        assert sorted(seen) == ["/events","/ready"]
        catalog = (await gateway.get("/api/services")).json()["services"]
        assert next(s for s in catalog if s["id"] == sid)["status"] == "degraded"

        # Removing approval revokes existing registrations without making another request.
        monkeypatch.setenv("APPROVED_HTTPS_ORIGINS", "")
        assert (await gateway.get(f"/api/services/{sid}/health")).status_code == 403
        assert sorted(seen) == ["/events","/ready"]
