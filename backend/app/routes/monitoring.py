from typing import Annotated

from fastapi import APIRouter, Query

from .. import external, registry, scenarios, services
from ..models import (
    ServiceHealth,
    ServiceLogs,
    ServiceMetrics,
    ServicesResponse,
    ServiceSummary,
)

router = APIRouter(prefix="/api/services", tags=["monitoring"])


@router.get("", response_model=ServicesResponse)
def list_services() -> ServicesResponse:
    records = []
    for original in services.load_services():
        row = scenarios.state(original.id)
        records.append(scenarios.snapshot(original, row)[0] if row else original)
    for app in registry.registered():
        records.append(
            ServiceSummary(
                id=app["service_ids"][0],
                name=app["name"],
                status=app.get("last_status", "unknown"),
            )
        )
    return ServicesResponse(services=records)


@router.get("/{id}/health", response_model=ServiceHealth)
async def service_health(id: str) -> ServiceHealth:
    if app := registry.for_service(id):
        return await registry.read_health(app)
    service = services.get_service(id)
    if row := scenarios.state(id):
        service = scenarios.snapshot(service, row)[0]
    return ServiceHealth(
        service_id=service.id,
        status=service.status,
        http_status=service.http_status,
        message=service.message,
    )


@router.get("/{id}/logs", response_model=ServiceLogs)
async def service_logs(
    id: str, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> ServiceLogs:
    if app := registry.for_service(id):
        result = await external.fetch_external(app, "logs")
        result.logs = sorted(
            result.logs, key=lambda entry: entry.timestamp, reverse=True
        )[:limit]
        return result
    if row := scenarios.state(id):
        return ServiceLogs(
            service_id=id,
            logs=scenarios.snapshot(services.get_service(id), row)[1][:limit],
        )
    return ServiceLogs(service_id=id, logs=services.get_logs(id, limit))


@router.get(
    "/{id}/metrics", response_model=ServiceMetrics, response_model_exclude_none=True
)
async def service_metrics(id: str) -> ServiceMetrics:
    if app := registry.for_service(id):
        return await external.fetch_external(app, "metrics")
    if row := scenarios.state(id):
        return ServiceMetrics(
            service_id=id, metrics=scenarios.snapshot(services.get_service(id), row)[2]
        )
    return ServiceMetrics(service_id=id, metrics=services.get_metrics(id))
