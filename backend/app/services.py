import logging
from pathlib import Path
from typing import TypeVar

from pydantic import Field, TypeAdapter, ValidationError

from .models import LogEntry, Metrics, NonBlankText, ServiceStatus, ServiceSummary

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().with_name("data")
Fixture = TypeVar("Fixture")


class FixtureError(Exception):
    """A monitoring fixture could not provide the requested data."""


class ServiceNotFound(Exception):
    """The monitoring catalog has no service with the requested ID."""


class ServiceRecord(ServiceSummary):
    status: ServiceStatus
    http_status: int = Field(ge=100, le=599)
    message: NonBlankText


SERVICES = TypeAdapter(list[ServiceRecord])
LOGS = TypeAdapter(dict[str, list[LogEntry]])
METRICS = TypeAdapter(dict[str, Metrics])


def load_fixture(filename: str, schema: TypeAdapter[Fixture]) -> Fixture:
    try:
        return schema.validate_json((DATA_DIR / filename).read_bytes())
    except (OSError, ValidationError) as exc:
        logger.error(
            "monitoring_fixture_invalid file=%s error_type=%s",
            filename,
            type(exc).__name__,
        )
        raise FixtureError(f"Monitoring data is unavailable ({filename}).") from exc


def load_services() -> list[ServiceRecord]:
    records = load_fixture("services.json", SERVICES)
    if not records or len({record.id for record in records}) != len(records):
        logger.error("monitoring_catalog_invalid")
        raise FixtureError("Monitoring data is unavailable (services.json).")
    return records


def get_service(service_id: str) -> ServiceRecord:
    for record in load_services():
        if record.id == service_id:
            return record
    raise ServiceNotFound("Service was not found.")


def load_service_data(
    service_id: str, filename: str, schema: TypeAdapter[dict[str, Fixture]]
) -> Fixture:
    get_service(service_id)
    records = load_fixture(filename, schema)
    if service_id not in records:
        logger.error("monitoring_fixture_missing_service file=%s service=%s", filename, service_id)
        raise FixtureError(f"Monitoring data is unavailable ({filename}).")
    return records[service_id]


def get_logs(service_id: str, limit: int) -> list[LogEntry]:
    entries = load_service_data(service_id, "logs.json", LOGS)
    return sorted(entries, key=lambda entry: entry.timestamp, reverse=True)[:limit]


def get_metrics(service_id: str) -> Metrics:
    return load_service_data(service_id, "metrics.json", METRICS)
