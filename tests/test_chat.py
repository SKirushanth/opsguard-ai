import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool

from agent.graph import build_graph
from agent.main import app as agent_app
from agent.tools import build_monitoring_tools
from backend.app.main import app as backend_app
from backend.app.routes.chat import get_agent_client

AGENT_URL = "http://agent.example.test/chat"
PAYLOAD = {"message": "Why is the order service failing?", "session_id": "test-001"}


@pytest.fixture
def model_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "")


@pytest.mark.usefixtures("model_disabled")
@pytest.mark.parametrize("message", ["", "  ", "x" * 4001])
def test_invalid_chat_messages(message: str) -> None:
    for app, path in [(backend_app, "/api/chat"), (agent_app, "/chat")]:
        with TestClient(app) as client:
            response = client.post(path, json={**PAYLOAD, "message": message})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "message"]


@pytest.mark.usefixtures("model_disabled")
def test_agent_health_and_missing_model() -> None:
    with TestClient(agent_app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.post("/chat", json={**PAYLOAD, "context": []}).status_code == 422
        assert client.post("/chat", json={**PAYLOAD, "session_id": " "}).status_code == 422
        response = client.post("/chat", json=PAYLOAD)
    assert response.status_code == 503
    assert response.json() == {"detail": "The agent requires LLM_API_KEY."}


@pytest.mark.anyio
async def test_backend_forwards_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_CHAT_URL", AGENT_URL)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == AGENT_URL
        assert json.loads(request.content) == {**PAYLOAD, "context": {}}
        return httpx.Response(200, json={"response": "Investigation results."})

    async with (
        backend_app.router.lifespan_context(backend_app),
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://backend") as gateway,
    ):
        monkeypatch.setitem(backend_app.dependency_overrides, get_agent_client, lambda: upstream)
        response = await gateway.post("/api/chat", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"response": "Investigation results.", "session_id": "test-001"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (httpx.ConnectError("secret upstream details"), 503),
        (httpx.ReadTimeout("secret upstream details"), 504),
        (httpx.Response(503, json={"detail": "secret upstream details"}), 503),
        (httpx.Response(504), 504),
        (httpx.Response(500), 502),
        (httpx.Response(200, json={"response": "  "}), 502),
        (httpx.Response(200, content="not valid JSON"), 502),
    ],
)
async def test_backend_handles_agent_failures(
    failure: httpx.Response | Exception, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CHAT_URL", AGENT_URL)

    def respond(_request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return failure

    async with (
        backend_app.router.lifespan_context(backend_app),
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://backend") as gateway,
    ):
        monkeypatch.setitem(backend_app.dependency_overrides, get_agent_client, lambda: upstream)
        response = await gateway.post("/api/chat", json=PAYLOAD)
    assert response.status_code == status
    assert "secret upstream details" not in response.text


class ScriptedModel(BaseChatModel):
    reply: Callable[[list[BaseMessage]], AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted-test-model"

    def bind_tools(self, tools: list[BaseTool], **_kwargs: Any) -> BaseChatModel:
        assert [tool.name for tool in tools] == [
            "get_service_health", "get_service_logs", "get_service_metrics",
        ]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **_kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.reply(messages))])


@pytest.mark.usefixtures("model_disabled")
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("message", "service", "resources", "offline", "answer"),
    [
        (
            PAYLOAD["message"], "orders", ("health", "logs", "metrics"), False,
            "Simulated evidence: HTTP 503, 42.0% errors, pool 100/100.",
        ),
        (
            "Is the auth service healthy?", "auth", ("health",), False,
            "Simulated evidence: auth is healthy.",
        ),
        (
            "Investigate the billing service.", "billing", ("health",), False,
            "No monitored service with ID 'billing' is registered.",
        ),
        (
            PAYLOAD["message"], "orders", ("health",), True,
            "The monitoring API is unreachable.",
        ),
    ],
    ids=["orders-investigation", "auth-health", "unknown-billing", "monitoring-unreachable"],
)
async def test_agent_chat_runs_graph_and_monitoring_tools(
    message: str, service: str, resources: tuple[str, ...], offline: bool, answer: str
) -> None:
    def reply(messages: list[BaseMessage]) -> AIMessage:
        assert isinstance(messages[0], SystemMessage)
        assert messages[1].content == message
        tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
        if not tool_messages:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": f"get_service_{resource}", "id": resource, "args": {"service": service}}
                    for resource in resources
                ],
            )
        assert {message.tool_call_id for message in tool_messages} == set(resources)
        if offline or service == "billing":
            assert tool_messages[0].status == "error"
            return AIMessage(content="The service is healthy.")

        assert all(message.status == "success" for message in tool_messages)
        evidence = {message.tool_call_id: json.loads(message.content) for message in tool_messages}
        assert evidence["health"]["service_id"] == service
        if service == "auth":
            return AIMessage(content=f"Simulated evidence: auth is {evidence['health']['status']}.")
        assert any("pool exhausted" in entry["message"] for entry in evidence["logs"]["logs"])
        health, metrics = evidence["health"], evidence["metrics"]["metrics"]
        return AIMessage(content=(
            f"Simulated evidence: HTTP {health['http_status']}, {metrics['error_rate_percent']}% errors, "
            f"pool {metrics['active_db_connections']}/{metrics['db_connection_limit']}."
        ))

    def disconnect(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    transport = httpx.MockTransport(disconnect) if offline else httpx.ASGITransport(app=backend_app)
    async with (
        agent_app.router.lifespan_context(agent_app),
        httpx.AsyncClient(transport=transport, base_url="http://monitoring") as monitoring,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app), base_url="http://agent") as client,
    ):
        agent_app.state.investigation_graph = build_graph(
            ScriptedModel(reply=reply), build_monitoring_tools(monitoring)
        )
        response = await client.post("/chat", json={**PAYLOAD, "message": message, "context": {}})

    assert response.status_code == 200
    assert response.json() == {"response": answer}
