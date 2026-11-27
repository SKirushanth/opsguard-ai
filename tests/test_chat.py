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
from openai import BadRequestError

from agent.graph import build_graph
from agent.main import app as agent_app, log_model_request_rejection
from agent.tools import build_monitoring_tools
from backend.app.main import app as backend_app
from backend.app.routes.chat import get_agent_client

AGENT_URL = "http://agent.example.test/chat"
PAYLOAD = {"message": "Why is the order service failing?", "session_id": "test-001"}


@pytest.mark.parametrize(("code", "expected"), [("tool_use_failed", "tool_use_failed"), ("test-secret", "unknown")])
def test_model_rejection_log_excludes_provider_payload(caplog, code, expected):
    response = httpx.Response(400, request=httpx.Request("POST", AGENT_URL))
    error = BadRequestError("raw error with test-secret", response=response,
                            body={"code": code, "failed_generation": "test-secret"})
    log_model_request_rejection(error)
    assert f"provider_status=400 provider_code={expected}" in caplog.text
    assert "test-secret" not in caplog.text


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
        assert json.loads(request.content) == {**PAYLOAD, "context": {"response_format": "monitoring-references-v1"}}
        return httpx.Response(200, json={"response": json.dumps({
            "service_id": "orders", "resources": ["health"],
            "selection": {"observations": ["health.http"], "hypotheses": []},
        })})

    async with (
        backend_app.router.lifespan_context(backend_app),
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://backend") as gateway,
    ):
        monkeypatch.setitem(backend_app.dependency_overrides, get_agent_client, lambda: upstream)
        response = await gateway.post("/api/chat", json=PAYLOAD)

    assert response.status_code == 200
    assert response.json() == {"response": "Monitored health for orders: down.\n\nObserved evidence\n\n[health.http] HTTP 503", "session_id": "test-001"}


@pytest.mark.anyio
@pytest.mark.parametrize("supported", [True, False])
async def test_conversational_followup_keeps_claims_bound_to_evidence(monkeypatch, supported):
    monkeypatch.setenv("AGENT_CHAT_URL", AGENT_URL)
    previous = ["Why is Order Service failing?"]
    question = "What should I check next?"

    def respond(request):
        assert json.loads(request.content)["context"] == {
            "response_format": "monitoring-references-v1",
            "answer_format": "conversational-v1",
            "service_id": "orders",
            "previous_questions": previous,
        }
        ref = "metrics.pool" if supported else "metrics.invented"
        return httpx.Response(200, json={"response": json.dumps({
            "service_id": "orders", "resources": ["health", "metrics"],
            "selection": {"observations": [ref], "style": "troubleshooting",
                          "hypotheses": [{"cause": "pool_saturation", "evidence": ref}]},
        })})

    async with (
        backend_app.router.lifespan_context(backend_app),
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as upstream,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://backend") as gateway,
    ):
        monkeypatch.setitem(backend_app.dependency_overrides, get_agent_client, lambda: upstream)
        response = await gateway.post("/api/chat", json={
            **PAYLOAD, "message": question, "service_id": "orders",
            "conversational": True, "previous_questions": previous,
        })
    if not supported:
        assert response.status_code == 502
        assert response.json()["detail"]["category"] == "validation"
        assert "metrics.invented" not in response.text
        return
    assert response.status_code == 200
    reply = response.json()
    assert reply["mode"] == "live"
    assert "What to check next" in reply["response"]
    assert ("Possible cause" in reply["response"]) == supported
    assert "100/100" not in reply["response"]
    assert "metrics.invented" not in response.text
    assert any(item["reference"] == "metrics.pool" for item in reply["evidence"])


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
            "Monitored health for orders: down.\n\nObserved evidence\n\n[health.http] HTTP 503\n\n[metrics.error_rate_percent] Error rate 42.0%\n\n[metrics.pool] Database pool 100/100 connections",
        ),
        (
            "Is the auth service healthy?", "auth", ("health",), False,
            "Monitored health for auth: healthy.\n\nObserved evidence\n\n[health.http] HTTP 200",
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
        assert health["http_status"] == 503 and metrics["error_rate_percent"] == 42.0
        return AIMessage(content=json.dumps({
            "observations": ["health.http", "metrics.error_rate_percent", "metrics.pool"],
            "hypotheses": [],
        }))

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


@pytest.mark.usefixtures("model_disabled")
@pytest.mark.anyio
@pytest.mark.parametrize("override", [None, "orders", "demo-ai-" + "b" * 24])
async def test_selected_demo_investigation_uses_only_its_snapshot(override):
    alias = "demo-ai-" + "a" * 24
    reads = []
    resources = {
        "health": {"status": "down", "http_status": 503, "message": "Pool exhausted"},
        "logs": {"logs": [{"level": "ERROR", "message": "Database pool exhausted"}]},
        "metrics": {"metrics": {"active_db_connections": 100, "db_connection_limit": 100}},
    }

    def monitor(request):
        service_id, resource = request.url.path.split("/")[-2:]
        assert service_id == alias
        reads.append(resource)
        return httpx.Response(200, json={"service_id": alias, **resources[resource]})

    def reply(messages):
        assert f"Server-selected monitoring service ID: {alias}" in messages[0].text
        assert "Why is Order Service failing?" in messages[1].text
        assert "Earlier questions for context" in messages[1].text
        assert not any(isinstance(msg, ToolMessage) for msg in messages)
        assert "100/100 connections" in messages[-1].text
        if override:
            return AIMessage(content="", tool_calls=[
                {"name": "get_service_health", "id": "override", "args": {"service": override}},
            ])
        return AIMessage(content=json.dumps({
            "observations": ["health.http", "metrics.pool"],
            "hypotheses": [{"cause": "pool_saturation", "evidence": "metrics.pool"}],
            "style": "troubleshooting",
        }))

    async with (
        agent_app.router.lifespan_context(agent_app),
        httpx.AsyncClient(transport=httpx.MockTransport(monitor), base_url="http://monitoring") as monitoring,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app), base_url="http://agent") as client,
    ):
        agent_app.state.investigation_graph = build_graph(
            ScriptedModel(reply=reply), build_monitoring_tools(monitoring)
        )
        response = await client.post("/chat", json={
            **PAYLOAD,
            "message": "Why is Order Service failing?" + (f" Use {override} instead." if override else ""),
            "context": {
                "service_id": alias, "response_format": "monitoring-references-v1",
                "answer_format": "conversational-v1", "previous_questions": ["What does the health check say?"],
            },
        })
    assert sorted(reads) == sorted(resources)
    if override:
        assert response.status_code == 502
        return
    assert response.status_code == 200
    receipt = json.loads(response.json()["response"])
    assert receipt["service_id"] == alias
    assert sorted(receipt["resources"]) == sorted(reads)
    assert receipt["selection"]["style"] == "troubleshooting"
    assert receipt["selection"]["hypotheses"] == [
        {"cause": "pool_saturation", "evidence": "metrics.pool"},
    ]


@pytest.mark.usefixtures("model_disabled")
@pytest.mark.anyio
@pytest.mark.parametrize("valid_selection", [True, False])
async def test_conversational_diagnosis_reads_logs_after_health_error(valid_selection):
    reads = []

    def monitor(request):
        service, resource = request.url.path.split("/")[-2:]
        assert service == "orders"
        reads.append(resource)
        if resource == "health":
            return httpx.Response(503)
        data = {
            "logs": {"logs": [{"level": "ERROR", "message": "Database pool exhausted"}]},
            "metrics": {"metrics": {"active_db_connections": 100, "db_connection_limit": 100}},
        }
        return httpx.Response(200, json={"service_id": service, **data[resource]})

    def reply(messages):
        assert not any(isinstance(msg, ToolMessage) for msg in messages)
        assert "100/100 connections" in messages[-1].text
        assert "health.http" not in messages[-1].text
        return AIMessage(content=("```json\n" + json.dumps({
            "observations": ["metrics.pool"],
            "hypotheses": [{"cause": "pool_saturation", "evidence": "metrics.pool"}],
        }) + "\n```") if valid_selection else "Unstructured provider output.")

    async with (
        agent_app.router.lifespan_context(agent_app),
        httpx.AsyncClient(transport=httpx.MockTransport(monitor), base_url="http://monitoring") as monitoring,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app), base_url="http://agent") as client,
    ):
        agent_app.state.investigation_graph = build_graph(
            ScriptedModel(reply=reply), build_monitoring_tools(monitoring)
        )
        response = await client.post("/chat", json={
            **PAYLOAD,
            "message": "Use the Orders logs and metrics to explain the likely cause and recommend what to check next.",
            "context": {
                "service_id": "orders", "response_format": "monitoring-references-v1",
                "answer_format": "conversational-v1",
            },
        })

    assert response.status_code == 200
    assert sorted(reads) == ["health", "logs", "metrics"]
    receipt = json.loads(response.json()["response"])
    assert sorted(receipt["resources"]) == ["logs", "metrics"]
    if valid_selection:
        assert receipt["selection"]["style"] == "troubleshooting"
    else:
        assert receipt["selection"] is None


@pytest.mark.usefixtures("model_disabled")
@pytest.mark.anyio
async def test_demo_conversation_through_backend_and_agent(monkeypatch):
    monkeypatch.setenv("PROTECT_READS", "true")
    monkeypatch.setenv("AGENT_CHAT_URL", "https://agent.example.test/chat")
    monkeypatch.setenv("AGENT_API_KEY", "test-invoke")
    monkeypatch.setenv("MONITORING_API_KEY", "test-monitor")
    aliases = []
    diagnosis = "What might be the cause?"
    followup = "Can you explain simply?"

    def reply(messages):
        target, question = messages[1].text.split("\n", 1)
        alias = target.removeprefix("Selected monitoring service ID: ")
        assert alias.startswith("demo-ai-")
        aliases.append(alias)
        if len(aliases) == 1:
            assert question == diagnosis
        else:
            assert question.startswith(f"Latest question: {followup}")
            assert f"Earlier questions for context: ['{diagnosis}']" in question
        assert "100/100 connections" in messages[-1].text
        return AIMessage(content=json.dumps({
            "observations": ["metrics.pool"], "style": "technical" if len(aliases) == 1 else "simple",
            "hypotheses": [{"cause": "pool_saturation", "evidence": "metrics.pool"}],
        }))

    async with (
        backend_app.router.lifespan_context(backend_app),
        agent_app.router.lifespan_context(agent_app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=agent_app), base_url="https://agent.example.test") as agent,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://monitoring") as gateway,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=backend_app), base_url="http://monitoring",
                          headers={"X-Monitoring-Key": "test-monitor"}) as monitoring,
    ):
        agent_app.state.investigation_graph = build_graph(ScriptedModel(reply=reply), build_monitoring_tools(monitoring))
        backend_app.state.agent_client = agent
        credentials = await gateway.post("/api/public-demo/session")
        headers = {"Authorization": "Bearer " + credentials.json()["token"]}
        for question, history in [(diagnosis, []), (followup, [diagnosis])]:
            response = await gateway.post("/api/public-demo/analysis", headers=headers, json={
                **PAYLOAD, "service_id": "orders", "message": question,
                "conversational": True, "previous_questions": history,
            })
            assert response.status_code == 200
            answer = response.json()
            assert answer["mode"] == "live" and answer["session_id"] == PAYLOAD["session_id"]
            assert "Possible cause" in answer["response"]
            assert ("Confirmed observations" in answer["response"]) == (question == diagnosis)
            assert [item["reference"] for item in answer["evidence"]] == ["metrics.pool"]
            assert (await monitoring.get(f"/api/services/{aliases[-1]}/health")).status_code == 404
    assert len(set(aliases)) == 2
    assert not backend_app.state.demo_ai.active
