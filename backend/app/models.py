from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

ServiceStatus = Literal["healthy", "degraded", "down"]
NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ChatMessage = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
SessionID = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class ServiceSummary(BaseModel):
    model_config = ConfigDict(strict=True)

    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
    name: NonBlankText
    status: ServiceStatus | Literal["unknown"]


class ServicesResponse(BaseModel):
    services: list[ServiceSummary]


class ServiceHealth(BaseModel):
    model_config = ConfigDict(strict=True)

    service_id: str
    status: ServiceStatus
    http_status: int = Field(ge=100, le=599)
    message: NonBlankText


class LogEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    timestamp: AwareDatetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    message: NonBlankText


class ServiceLogs(BaseModel):
    service_id: str
    logs: list[LogEntry]


class Metrics(BaseModel):
    model_config = ConfigDict(strict=True)

    error_rate_percent: float = Field(ge=0, le=100)
    avg_latency_ms: int = Field(ge=0)
    requests_last_5m: int = Field(ge=0)
    active_db_connections: int | None = Field(default=None, ge=0)
    db_connection_limit: int | None = Field(default=None, ge=1)


class ServiceMetrics(BaseModel):
    service_id: str
    metrics: Metrics


ServiceID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class ChatRequest(BaseModel):
    service_id: ServiceID | None = None
    message: ChatMessage
    session_id: SessionID


class AgentReply(BaseModel):
    response: NonBlankText


class ChatResponse(AgentReply):
    session_id: SessionID
