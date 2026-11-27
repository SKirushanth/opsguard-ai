import logging
from typing import Any

import httpx
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel, Field

logger = logging.getLogger("uvicorn.error").getChild(__name__)
ALLOWED_HEALTH_STATUSES = {"healthy", "degraded", "down", "unknown"}


class ServiceInput(BaseModel):
    service: str = Field(
        pattern=r"^[a-z][a-z0-9-]{0,63}$",
        description="Exact server-selected monitoring service ID when provided. Temporary demo-ai- IDs are valid.",
    )


class LogsInput(ServiceInput):
    limit: int = Field(
        default=20, strict=True, ge=1, le=100, description="Maximum recent entries to return."
    )


def build_monitoring_tools(client: httpx.AsyncClient) -> list[BaseTool]:
    async def fetch(
        service: str,
        resource: str,
        required_fields: set[str],
        params: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        scope = "demo" if service.startswith("demo-ai-") else "workspace"
        logger.info("monitoring_tool_called resource=%s scope=%s", resource, scope)
        try:
            response = await client.get(f"api/services/{service}/{resource}", params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            logger.info("monitoring_tool_failed resource=%s reason=timeout", resource)
            raise ToolException("The monitoring API timed out.") from exc
        except httpx.RequestError as exc:
            logger.info("monitoring_tool_failed resource=%s reason=transport", resource)
            raise ToolException("The monitoring API is unreachable.") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.info("monitoring_tool_failed resource=%s reason=http_status status=%d", resource, status)
            if status == 501:
                raise ToolException(f"{resource.capitalize()} monitoring is unavailable for this service.") from exc
            if status == 404:
                raise ToolException(f"No monitored service with ID '{service}' is registered.") from exc
            raise ToolException(f"The monitoring API returned HTTP {status}.") from exc
        except ValueError as exc:
            logger.info("monitoring_tool_failed resource=%s reason=invalid_json", resource)
            raise ToolException("The monitoring API returned invalid JSON.") from exc

        if (
            not isinstance(data, dict)
            or data.get("service_id") != service
            or not required_fields <= data.keys()
        ):
            raise ToolException("The monitoring API returned an invalid response.")
        if resource == "health" and (not isinstance(data["status"], str) or data["status"] not in ALLOWED_HEALTH_STATUSES):
            raise ToolException("The monitoring API returned an invalid health status.")
        if resource == "logs" and not isinstance(data["logs"], list):
            raise ToolException("The monitoring API returned invalid logs.")
        if resource == "metrics" and not isinstance(data["metrics"], dict):
            raise ToolException("The monitoring API returned invalid metrics.")
        logger.info("monitoring_tool_succeeded resource=%s scope=%s", resource, scope)
        return data

    @tool(args_schema=ServiceInput)
    async def get_service_health(service: str) -> dict[str, Any]:
        """Get a monitored service's deterministic normalized health status, HTTP status, explanation, and sanitized evidence."""
        return await fetch(service, "health", {"status", "http_status", "message"})

    @tool(args_schema=LogsInput)
    async def get_service_logs(service: str, limit: int = 20) -> dict[str, Any]:
        """Get recent application logs or sanitized log evidence for a monitored service, newest first."""
        return await fetch(service, "logs", {"logs"}, {"limit": limit})

    @tool(args_schema=ServiceInput)
    async def get_service_metrics(service: str) -> dict[str, Any]:
        """Get recognized performance metrics with known units and availability details for a monitored service."""
        return await fetch(service, "metrics", {"metrics"})

    return [get_service_health, get_service_logs, get_service_metrics]
