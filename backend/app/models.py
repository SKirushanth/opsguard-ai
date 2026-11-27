from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

ServiceStatus = Literal["healthy", "degraded", "down"]
NormalizedStatus = Literal["healthy", "degraded", "down", "unknown"]
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
    status: NormalizedStatus


class ServicesResponse(BaseModel):
    services: list[ServiceSummary]


class ServiceHealth(BaseModel):
    model_config = ConfigDict(strict=True)

    service_id: str
    status: NormalizedStatus
    http_status: int | None = Field(default=None, ge=100, le=599)
    message: NonBlankText
    reason: str | None = None
    requires_mapping: bool = False
    normalization_method: str = "native"
    explanation: str | None = None
    evidence: dict[str, Any] | list[Any] | str | None = None
    last_checked_at: AwareDatetime | None = None
    error_category: str | None = None


class LogEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    timestamp: AwareDatetime | None = None
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNKNOWN"]
    message: NonBlankText


class ServiceLogs(BaseModel):
    service_id: str
    logs: list[LogEntry]
    format: Literal["json", "text", "unrecognized"] | None = None
    available: bool | None = None
    raw_evidence: dict[str, Any] | list[Any] | str | None = None
    explanation: str | None = None


class Metrics(BaseModel):
    model_config = ConfigDict(strict=True)

    error_rate_percent: float = Field(ge=0, le=100)
    avg_latency_ms: int = Field(ge=0)
    requests_last_5m: int = Field(ge=0)
    active_db_connections: int | None = Field(default=None, ge=0)
    db_connection_limit: int | None = Field(default=None, ge=1)


class NormalizedMetrics(BaseModel):
    model_config = ConfigDict(strict=True)

    error_rate_percent: float | None = Field(default=None, ge=0, le=100)
    avg_latency_ms: int | None = Field(default=None, ge=0)
    requests_last_5m: int | None = Field(default=None, ge=0)
    active_db_connections: int | None = Field(default=None, ge=0)
    db_connection_limit: int | None = Field(default=None, ge=1)


class ServiceMetrics(BaseModel):
    service_id: str
    metrics: Metrics | NormalizedMetrics
    available: bool | None = None
    unavailable_fields: list[str] | None = None
    unrecognized_fields: list[str] | None = None
    raw_evidence: dict[str, Any] | list[Any] | str | None = None
    explanation: str | None = None


ServiceID = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class ChatRequest(BaseModel):
    service_id: ServiceID | None = None
    message: ChatMessage
    session_id: SessionID
    conversational: bool = False
    previous_questions: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=3
    )


class AgentReply(BaseModel):
    response: NonBlankText


class ChatResponse(AgentReply):
    session_id: SessionID
    evidence: list[dict[str, str]] | None = None
