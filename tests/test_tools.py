import httpx
import pytest
from langchain_core.tools import ToolException
from pydantic import ValidationError

from agent.tools import build_monitoring_tools

BASE_URL = "https://monitor.example.test/monitoring/"
HEALTH = {"service_id": "orders", "status": "down", "http_status": 503, "message": "Unavailable"}
LOGS = {"service_id": "orders", "logs": [{"message": "Database connection timeout"}]}
METRICS = {"service_id": "orders", "metrics": {"error_rate_percent": 42.0}}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_index", "resource", "arguments", "params", "payload"),
    [
        (0, "health", {"service": "orders"}, {}, HEALTH),
        (1, "logs", {"service": "orders", "limit": 10}, {"limit": "10"}, LOGS),
        (1, "logs", {"service": "orders"}, {"limit": "20"}, LOGS),
        (2, "metrics", {"service": "orders"}, {}, METRICS),
    ],
)
async def test_tool_returns_http_response(
    tool_index: int, resource: str, arguments: dict, params: dict, payload: dict
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.copy_with(query=None) == f"{BASE_URL}api/services/orders/{resource}"
        assert dict(request.url.params) == params
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(respond)) as client:
        tool = build_monitoring_tools(client)[tool_index]
        assert tool.name == f"get_service_{resource}"
        assert await tool.ainvoke(arguments) == payload


@pytest.mark.anyio
async def test_unknown_service() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/billing/health")
        return httpx.Response(404, json={"detail": "Service was not found."})

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(respond)) as client:
        health, *_ = build_monitoring_tools(client)
        with pytest.raises(ToolException, match="billing.*registered"):
            await health.ainvoke({"service": "billing"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(302, headers={"Location": "https://other.example.test"}), "HTTP 302"),
        (httpx.Response(200, content=b"{oops"), "invalid JSON"),
        (httpx.Response(200, json={"service_id": "auth"}), "invalid response"),
        (httpx.ConnectError("offline"), "unreachable"),
        (httpx.ReadTimeout("slow"), "timed out"),
    ],
)
async def test_failed_tools_raise(failure: httpx.Response | Exception, message: str) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return failure

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(respond)) as client:
        health, *_ = build_monitoring_tools(client)
        with pytest.raises(ToolException, match=message):
            await health.ainvoke({"service": "orders"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [{"service": "../orders"}, {"service": "https://example.test"}, {"service": "orders", "limit": 101}],
)
async def test_invalid_tool_inputs_never_call_http(arguments: dict) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid arguments reached the HTTP client")

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(respond)) as client:
        _, logs, _ = build_monitoring_tools(client)
        with pytest.raises(ValidationError):
            await logs.ainvoke(arguments)
