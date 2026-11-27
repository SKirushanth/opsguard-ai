"""The model selects evidence IDs. Only application code renders monitoring claims."""

import hashlib
import json
from dataclasses import dataclass, field
from itertools import islice
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

REFERENCE_FORMAT = "monitoring-references-v1"
CLARIFICATION = "Which monitored service would you like to investigate?"
Resource = Literal["health", "logs", "metrics"]
ServiceID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
Reference = Annotated[str, Field(pattern=r"^[a-z0-9_.]{1,48}$")]
Cause = Literal["pool_saturation", "connection_refused", "smtp_timeout", "token_validation", "reported_error"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Hypothesis(Contract):
    cause: Cause
    evidence: Reference


class Selection(Contract):
    observations: list[Reference] = Field(max_length=8)
    hypotheses: list[Hypothesis] = Field(max_length=3)


class Investigation(Contract):
    """Private agent-to-gateway contract. Carries no health, measurements or prose."""
    service_id: ServiceID
    resources: list[Resource] = Field(min_length=1, max_length=3)
    selection: Selection | None = None


CAUSES = {
    "pool_saturation": ("Database pool saturation may be delaying requests.", "Inspect active connections and pool wait times."),
    "connection_refused": ("A refused dependency connection may be interrupting requests.", "Check the dependency address and whether it is accepting connections."),
    "smtp_timeout": ("An SMTP timeout may be delaying message delivery.", "Check the mail server's availability and delivery timeouts."),
    "token_validation": ("A token validation failure may be blocking authentication.", "Review token validity and the authentication provider's errors."),
    "reported_error": ("The logged operation may be contributing to the incident.", "Trace the reported operation and check related requests."),
}
METRIC_LABELS = {
    "error_rate_percent": ("Error rate", "%"), "avg_latency_ms": ("Average latency", " ms"),
    "requests_last_5m": ("Requests in five minutes", ""), "active_db_connections": ("Active database connections", ""),
    "db_connection_limit": ("Database connection limit", ""),
}


def raw_log_messages(value, depth=0):
    """Extract explicitly named messages from an already bounded, sanitized log sample."""
    if depth >= 4:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in ("message", "error", "exception") and isinstance(child, str):
                yield {"message": child, "level": "UNKNOWN"}
            elif isinstance(child, (dict, list)):
                yield from raw_log_messages(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from raw_log_messages(child, depth + 1)


@dataclass
class Evidence:
    service_id: str
    status: str = "unknown"
    needs_mapping: bool = False
    facts: dict[str, str] = field(default_factory=dict)
    supports: dict[str, set[str]] = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)

    @classmethod
    def collect(cls, service_id: str, observations: dict, errors: list[str]):
        result = cls(service_id, unavailable=errors)
        health = observations.get("health", {})
        result.status = health.get("status", "unknown")
        result.needs_mapping = health.get("requires_mapping") is True
        code = health.get("http_status")
        if type(code) is int:
            result.facts["health.http"] = f"HTTP {code}"
        if health.get("error_category") in ("authentication_failure", "connectivity_failure", "endpoint_error"):
            result.facts["health.check"] = {
                "authentication_failure": "The monitoring endpoint rejected authentication.",
                "connectivity_failure": "The health check could not reach the monitoring endpoint.",
                "endpoint_error": "The health endpoint returned an unusable response.",
            }[health["error_category"]]
        metrics = observations.get("metrics", {})
        values = metrics.get("metrics", {}) if metrics.get("available") is not False else {}
        for key, (label, suffix) in METRIC_LABELS.items():
            if type(value := values.get(key)) in (int, float):
                result.facts["metrics." + key] = f"{label} {value}{suffix}"
        active, limit = values.get("active_db_connections"), values.get("db_connection_limit")
        if type(active) is int and type(limit) is int and limit > 0:
            result.facts["metrics.pool"] = f"Database pool {active}/{limit} connections"
            if active >= limit:
                result.supports["metrics.pool"] = {"pool_saturation"}
        logs = observations.get("logs", {})
        entries = logs.get("logs", []) if logs.get("available") is not False else []
        if not entries and logs.get("format") == "unrecognized" and logs.get("available") is not False:
            entries = list(islice(raw_log_messages(logs.get("raw_evidence")), 20))
        for index, entry in enumerate(entries[:20], 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("message"), str):
                continue
            # Bound and quote the source as a Markdown code block. Its content never
            # becomes a heading, link, instruction or model-authored conclusion.
            message = " ".join(entry["message"].split())[:300]
            if not message or message in ("[REDACTED]", "[TRUNCATED]"):
                continue
            event = json.dumps([entry.get("timestamp"), entry.get("level"), message], default=str)
            ref = "log." + hashlib.sha256(event.encode("utf-8")).hexdigest()[:16]
            result.facts[ref] = f"Log {index} (source text)\n\n    {message}"
            if entry.get("level") in ("ERROR", "CRITICAL"):
                result.supports[ref] = {"reported_error"}
                diagnostic = message.removeprefix(f"HTTP {code} · ").lower()
                for prefix, cause in (("connection refused", "connection_refused"),
                                      ("smtp connection timeout", "smtp_timeout"),
                                      ("token validation failed", "token_validation")):
                    if diagnostic.startswith(prefix):
                        result.supports[ref].add(cause)
        return result

    @property
    def diagnostic(self):
        return any(ref.startswith(("metrics.", "log.")) for ref in self.facts)

    def accepts(self, selection: Selection) -> bool:
        refs = set(selection.observations)
        if not refs or not refs.issubset(self.facts):
            return False
        return all(
            h.evidence in refs and h.cause in self.supports.get(h.evidence, ())
            for h in selection.hypotheses
        )

    def render(self, selection: Selection | None, *, draft: bool = False) -> str:
        # Revalidate at render time: the gateway may have fresher observations.
        if selection is not None and not self.accepts(selection):
            selection = None
        refs = selection.observations if selection else list(self.facts)[:8]
        hypotheses = selection.hypotheses if selection else []
        observations = [f"[{ref}] {self.facts[ref]}" for ref in dict.fromkeys(refs)]
        causes = [f"{CAUSES[h.cause][0]} [{h.evidence}]" for h in hypotheses]
        header = f"Monitored health for {self.service_id}: {self.status}."
        if self.needs_mapping:
            header += " A custom response mapping is required."
        if self.status == "unknown" and not self.diagnostic:
            limitation = "Unable to determine the application's health or likely root cause from the available monitoring evidence."
            return "\n\n".join([header, *observations, limitation, *self.unavailable])
        if draft:
            if not observations:
                return header
            return json.dumps({
                "title": f"Investigation of {self.service_id}",
                "description": header,
                "severity": {"down": "high", "degraded": "medium"}.get(self.status, "low"),
                "evidence": observations, "likely_root_cause": " ".join(causes) or "Cause undetermined.",
                "recommendations": list(dict.fromkeys(CAUSES[h.cause][1] for h in hypotheses)) or ["Review the collected observations."],
            })
        sections = [header]
        if observations:
            sections += ["Observed evidence", *observations]
        if causes:
            sections += ["Possible causes", *causes]
        return "\n\n".join([*sections, *self.unavailable])
