"""Deterministic monitoring response normalization, custom mapping, and sanitization."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    model_validator,
)

from .models import (
    LogEntry,
    Metrics,
    NormalizedMetrics,
    NormalizedStatus,
    ServiceHealth,
    ServiceLogs,
    ServiceMetrics,
)

SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|access[_-]?key|bearer)",
    re.IGNORECASE,
)
SECRET_INLINE_PATTERNS = [
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}"),
    re.compile(r"(?i)\b(authorization\s*[:=]\s*)([^\s,;\"']{4,})"),
    re.compile(r"(?i)\b((?:password|passwd|secret|token|api[_-]?key|apikey)\s*[:=]\s*)([^\s,;\"']{3,})"),
]
ISO_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\s+"
    r"(?:\[(?P<lvl_b>[A-Za-z]+)\]|(?P<lvl>[A-Za-z]+):?)\s+(?P<msg>.+)$"
)
LEVEL_PREFIX_RE = re.compile(r"^(?:\[(?P<lvl_b>[A-Za-z]+)\]|(?P<lvl>[A-Za-z]+):)\s+(?P<msg>.+)$")

HEALTHY_TOKENS = {"up", "ok", "healthy", "pass", "passing"}
DEGRADED_TOKENS = {"degraded", "warn", "warning", "partial"}
DOWN_TOKENS = {"down", "error", "fail", "failing", "unhealthy", "out_of_service", "critical"}
UNKNOWN_TOKENS = {"unknown"}

WRAPPER_KEYS = ("health", "service", "data", "result", "check")
COMPONENT_KEYS = ("components", "details", "checks", "dependencies")

LEVEL_ALIASES: dict[str, Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = {
    "debug": "DEBUG",
    "trace": "DEBUG",
    "info": "INFO",
    "notice": "INFO",
    "warn": "WARNING",
    "warning": "WARNING",
    "err": "ERROR",
    "error": "ERROR",
    "crit": "CRITICAL",
    "critical": "CRITICAL",
    "fatal": "CRITICAL",
}

MAX_EVIDENCE_DEPTH = 5
MAX_EVIDENCE_ITEMS = 40
MAX_STRING_LEN = 500
MAX_LOG_LINES = 100

MappingValue = StrictStr | StrictBool


class ServiceIdMismatchError(ValueError):
    """Raised when an external monitoring payload declares a different service_id."""


def _canonical_match_key(value: str | bool) -> tuple[str, str | bool]:
    if isinstance(value, bool):
        return ("bool", value)
    return ("str", value.strip().lower())


def _canonical_conflict_key(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value.strip().lower()


class CustomHealthMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status_field: str = Field(min_length=1, max_length=200)
    healthy_values: list[MappingValue] = Field(default_factory=list, max_length=20)
    down_values: list[MappingValue] = Field(default_factory=list, max_length=20)
    degraded_values: list[MappingValue] = Field(default_factory=list, max_length=20)

    def classify(self, value: Any) -> NormalizedStatus | None:
        if isinstance(value, (str, bool)):
            key = _canonical_match_key(value)
            for status, values in (("healthy", self.healthy_values), ("down", self.down_values),
                                   ("degraded", self.degraded_values)):
                if any(_canonical_match_key(candidate) == key for candidate in values):
                    return status
        return None

    @model_validator(mode="after")
    def validate_mapping(self) -> CustomHealthMapping:
        field = self.status_field.strip()
        if not field or field.startswith(".") or field.endswith(".") or ".." in field:
            raise ValueError("Use a valid dot-separated field path such as 'application.currentState'.")
        segments = field.split(".")
        if any(not SEGMENT_RE.fullmatch(seg) for seg in segments):
            raise ValueError(
                "Field path segments may contain only letters, digits, underscores, and hyphens."
            )
        self.status_field = field

        buckets: dict[str, list[str | bool]] = {
            "healthy": self.healthy_values,
            "down": self.down_values,
            "degraded": self.degraded_values,
        }
        cleaned: dict[str, list[str | bool]] = {}
        seen_by_conflict_key: dict[str, str] = {}

        for status_name, raw_items in buckets.items():
            deduped: list[str | bool] = []
            seen_local: set[tuple[str, str | bool]] = set()
            for item in raw_items:
                if isinstance(item, str):
                    stripped = item.strip()
                    if not stripped:
                        raise ValueError(f"{status_name}_values must not contain blank strings.")
                    val: str | bool = stripped
                else:
                    val = item
                match_key = _canonical_match_key(val)
                conflict_key = _canonical_conflict_key(val)
                prev = seen_by_conflict_key.get(conflict_key)
                if prev is not None and prev != status_name:
                    raise ValueError(
                        f"A mapping value cannot be assigned to both '{prev}' and '{status_name}'."
                    )
                seen_by_conflict_key[conflict_key] = status_name
                if match_key not in seen_local:
                    seen_local.add(match_key)
                    deduped.append(val)
            cleaned[status_name] = deduped

        if not any(cleaned.values()):
            raise ValueError("Provide at least one mapped value for healthy, down, or degraded.")

        self.healthy_values = cleaned["healthy"]
        self.down_values = cleaned["down"]
        self.degraded_values = cleaned["degraded"]
        return self


def sanitize_text(text: str, secret_values: Sequence[str] = (), max_len: int = MAX_STRING_LEN) -> str:
    cleaned = "".join(ch for ch in text if ch in ("\n", "\t") or ord(ch) >= 32)
    for secret in sorted(secret_values, key=len, reverse=True):
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    for pattern in SECRET_INLINE_PATTERNS:
        if pattern.groups >= 2:
            cleaned = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]" if m.group(1).lower().startswith(("authorization", "password", "passwd", "secret", "token", "api")) else f"{m.group(1)} [REDACTED]", cleaned)
        else:
            cleaned = pattern.sub(lambda m: f"{m.group(1)} [REDACTED]", cleaned)
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned


def sanitize_value(value: Any, secret_values: Sequence[str] = (), depth: int = 0) -> Any:
    if depth >= MAX_EVIDENCE_DEPTH:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value, secret_values)
    if isinstance(value, list):
        items = [sanitize_value(item, secret_values, depth + 1) for item in value[:MAX_EVIDENCE_ITEMS]]
        if len(value) > MAX_EVIDENCE_ITEMS:
            items.append(f"... ({len(value) - MAX_EVIDENCE_ITEMS} more items)")
        return items
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= MAX_EVIDENCE_ITEMS:
                result["_truncated"] = f"{len(value) - MAX_EVIDENCE_ITEMS} more keys"
                break
            key_str = sanitize_text(str(k), secret_values, 120)
            if SENSITIVE_KEY_RE.search(str(k)):
                result[key_str] = "[REDACTED]"
            else:
                result[key_str] = sanitize_value(v, secret_values, depth + 1)
        return result
    return sanitize_text(str(value), secret_values)


def sanitize_raw_bytes(body: bytes | None, secret_values: Sequence[str] = ()) -> dict[str, Any] | list[Any] | str | None:
    if not body:
        return None
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        if text.startswith(("{", "[")):
            return "[Unparseable JSON omitted]"
        return sanitize_text(text, secret_values, 1500)
    sanitized = sanitize_value(parsed, secret_values)
    if isinstance(sanitized, (dict, list, str)):
        return sanitized
    return str(sanitized)


def _interpret_status_token(value: str) -> NormalizedStatus | None:
    token = value.strip().lower()
    if token in HEALTHY_TOKENS:
        return "healthy"
    if token in DEGRADED_TOKENS:
        return "degraded"
    if token in DOWN_TOKENS:
        return "down"
    if token in UNKNOWN_TOKENS:
        return "unknown"
    return None


class AmbiguousHealth(ValueError):
    """The supported indicators cannot establish one consistent health result."""


def _health_indicator(payload: dict) -> NormalizedStatus | None:
    remaining = 128

    def visit(node: dict, depth: int) -> NormalizedStatus | None:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 8:
            raise AmbiguousHealth("The health indicators exceed the traversal limit.")
        direct = set()
        if "status" in node:
            value = node["status"]
            status = _interpret_status_token(value) if isinstance(value, str) else None
            if status is None:
                raise AmbiguousHealth("A status field contains an unrecognized health indicator.")
            direct.add(status)
        if "healthy" in node:
            if not isinstance(node["healthy"], bool):
                raise AmbiguousHealth("A healthy field contains a non-boolean indicator.")
            direct.add("healthy" if node["healthy"] else "down")
        for key in WRAPPER_KEYS:
            child = node.get(key)
            if isinstance(child, dict) and (status := visit(child, depth + 1)) is not None:
                direct.add(status)
        if len(direct) > 1:
            raise AmbiguousHealth("Recognized health indicators disagree.")

        components = set()
        for key in COMPONENT_KEYS:
            collection = node.get(key)
            if collection is None:
                continue
            if not isinstance(collection, dict) or len(collection) > remaining:
                raise AmbiguousHealth("The component structure is unsupported or too large.")
            for child in collection.values():
                # Spring Boot details can also contain metadata such as disk size.
                if key == "details" and not isinstance(child, dict):
                    continue
                value = visit(child, depth + 1) if isinstance(child, dict) else None
                if value is None and key != "details":
                    raise AmbiguousHealth("A component has no recognized health indicator.")
                if value is not None:
                    components.add(value)
        if direct:
            aggregate = direct.pop()
            # A failing aggregate can legitimately include healthy dependencies.
            allowed = {"healthy": {"healthy"}, "degraded": {"healthy", "degraded"},
                       "down": {"healthy", "degraded", "down"}, "unknown": {"unknown"}}
            if components - allowed[aggregate]:
                raise AmbiguousHealth("The reported health conflicts with component health.")
            return aggregate
        if len(components) > 1:
            raise AmbiguousHealth("Component health indicators disagree.")
        return next(iter(components), None)

    return visit(payload, 0)


def _lookup_nested_field(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    curr: Any = payload
    for seg in path.split("."):
        if not isinstance(curr, dict) or seg not in curr:
            return False, None
        curr = curr[seg]
    return True, curr


def _apply_custom_mapping(
    payload: Any,
    mapping: CustomHealthMapping,
    *,
    service_id: str,
    http_status: int,
    sanitized_payload: dict[str, Any] | list[Any] | str | None,
    now: datetime,
) -> ServiceHealth:
    if not isinstance(payload, dict):
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message="The configured status field was not found because the response is not a JSON object.",
            reason="Configured status field is missing from health response",
            requires_mapping=True,
            normalization_method="custom_mapping",
            explanation="Expected a JSON object for custom field mapping.",
            evidence=sanitized_payload,
            last_checked_at=now,
            error_category="mapping_mismatch",
        )

    found, raw_val = _lookup_nested_field(payload, mapping.status_field)
    if not found:
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message="The configured status field was not found in the health response.",
            reason="Configured status field is missing from health response",
            requires_mapping=True,
            normalization_method="custom_mapping",
            explanation="The custom mapping field is missing from the response.",
            evidence=sanitized_payload,
            last_checked_at=now,
            error_category="mapping_mismatch",
        )

    if not isinstance(raw_val, (str, bool)):
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message=f"The configured field has unsupported type {type(raw_val).__name__}.",
            reason="Configured status value did not match any mapped classification",
            requires_mapping=True,
            normalization_method="custom_mapping",
            explanation=f"Custom mapping supports string and boolean values, got {type(raw_val).__name__}.",
            evidence=sanitized_payload,
            last_checked_at=now,
            error_category="mapping_mismatch",
        )

    if target_status := mapping.classify(raw_val):
        return ServiceHealth(
            service_id=service_id,
            status=target_status,
            http_status=http_status,
            message=f"Custom mapping reports {target_status}.",
            reason=None,
            requires_mapping=False,
            normalization_method="custom_mapping",
            explanation=f"The configured field matched the {target_status} classification.",
            evidence=sanitized_payload,
            last_checked_at=now,
            error_category=None,
        )

    return ServiceHealth(
        service_id=service_id,
        status="unknown",
        http_status=http_status,
        message="The configured field did not match any configured classification.",
        reason="Configured status value did not match any mapped classification",
        requires_mapping=True,
        normalization_method="custom_mapping",
        explanation="The observed field did not match any configured healthy, degraded, or down value.",
        evidence=sanitized_payload,
        last_checked_at=now,
        error_category="mapping_mismatch",
    )


def _auto_detect_health(
    payload: Any,
    *,
    service_id: str,
    http_status: int,
    sanitized_payload: dict[str, Any] | list[Any] | str | None,
    now: datetime,
) -> ServiceHealth:
    def unknown_unrecognized(reason: str, explanation: str, category: str = "unrecognized_format") -> ServiceHealth:
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message=explanation,
            reason=reason,
            requires_mapping=True,
            normalization_method="unrecognized",
            explanation=explanation,
            evidence=sanitized_payload,
            last_checked_at=now,
            error_category=category,
        )

    if not isinstance(payload, dict) or not payload:
        return unknown_unrecognized(
            "Unrecognized health response format",
            "Automatic detection did not find a recognized health indicator in the response.",
        )

    try:
        detected = _health_indicator(payload)
    except AmbiguousHealth as exc:
        return unknown_unrecognized("Unrecognized health response format", str(exc), "contradictory_indicators")
    if detected is None:
        return unknown_unrecognized(
            "Unrecognized health response format",
            "Automatic detection did not find a recognized health indicator in the response.",
        )

    # 1. Native OpsGuard health contract
    if {"service_id", "http_status", "message"} <= payload.keys() and payload.get("status") in (
        "healthy",
        "degraded",
        "down",
    ):
        try:
            native = ServiceHealth.model_validate(
                {
                    "service_id": service_id,
                    "status": payload["status"],
                    "http_status": payload["http_status"],
                    "message": sanitized_payload.get("message") if isinstance(sanitized_payload, dict) else None,
                }
            )
            return native.model_copy(
                update={
                    "normalization_method": "native",
                    "explanation": f"Native OpsGuard health payload reports '{native.status}'.",
                    "evidence": sanitized_payload,
                    "last_checked_at": now,
                    "requires_mapping": False,
                }
            )
        except ValidationError:
            pass

    return ServiceHealth(
        service_id=service_id, status=detected, http_status=http_status,
        message=f"Health endpoint reports '{detected}'.",
        reason="Health endpoint explicitly reported 'unknown'" if detected == "unknown" else None,
        normalization_method="auto", explanation=f"Recognized health indicators report '{detected}'.",
        evidence=sanitized_payload, last_checked_at=now,
    )


def normalize_health_response(
    *,
    service_id: str,
    http_status: int | None,
    body: bytes | None,
    network_error: str | None = None,
    mapping: CustomHealthMapping | None = None,
    secret_values: Sequence[str] = (),
    expected_local_id: str | None = None,
) -> ServiceHealth:
    """Deterministically normalize a monitoring health response in strict priority order."""
    now = datetime.now(UTC)

    # 1. Evaluate network and HTTP errors first.
    if network_error:
        safe_err = sanitize_text(network_error, secret_values, 300)
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=None,
            message=f"Monitoring endpoint connectivity failure: {safe_err}",
            reason=safe_err,
            requires_mapping=False,
            normalization_method="network_error",
            explanation=f"Monitoring endpoint could not be reached ({safe_err}); application health is unknown.",
            evidence=None,
            last_checked_at=now,
            error_category="connectivity_failure",
        )

    if http_status is None:
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=None,
            message="Monitoring endpoint did not return an HTTP status.",
            reason="Missing HTTP status",
            requires_mapping=False,
            normalization_method="network_error",
            explanation="No HTTP response was received from the monitoring endpoint.",
            evidence=None,
            last_checked_at=now,
            error_category="connectivity_failure",
        )

    if http_status in (401, 403):
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message=f"Monitoring endpoint authentication failed (HTTP {http_status}).",
            reason=f"Authentication failed (HTTP {http_status})",
            requires_mapping=False,
            normalization_method="http_error",
            explanation=f"Monitoring endpoint returned HTTP {http_status}, indicating an authentication or authorization failure.",
            evidence=None,
            last_checked_at=now,
            error_category="authentication_failure",
        )

    if 300 <= http_status < 400:
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message=f"Monitoring endpoint returned blocked redirect (HTTP {http_status}).",
            reason=f"Unsafe redirect blocked (HTTP {http_status})",
            requires_mapping=False,
            normalization_method="http_error",
            explanation=f"Monitoring endpoint returned HTTP {http_status}; redirects are blocked for security.",
            evidence=None,
            last_checked_at=now,
            error_category="endpoint_error",
        )

    if http_status >= 400 and http_status != 503:
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message=f"Monitoring endpoint returned HTTP {http_status}.",
            reason=f"Monitoring endpoint error (HTTP {http_status})",
            requires_mapping=False,
            normalization_method="http_error",
            explanation=f"Monitoring endpoint returned HTTP {http_status}; service health cannot be reliably determined.",
            evidence=None,
            last_checked_at=now,
            error_category="endpoint_error",
        )

    # Parse JSON body (for 2xx or 503)
    raw_bytes = body or b""
    try:
        payload = json.loads(raw_bytes)
    except (ValueError, RecursionError):
        if http_status == 503:
            return ServiceHealth(
                service_id=service_id,
                status="unknown",
                http_status=503,
                message="Monitoring endpoint returned HTTP 503 without valid JSON.",
                reason="Monitoring endpoint error (HTTP 503)",
                requires_mapping=False,
                normalization_method="http_error",
                explanation="Monitoring endpoint returned HTTP 503 with non-JSON content.",
                evidence=None,
                last_checked_at=now,
                error_category="endpoint_error",
            )
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=http_status,
            message="Monitoring endpoint returned malformed JSON.",
            reason="Malformed JSON response",
            requires_mapping=False,
            normalization_method="http_error",
            explanation=f"Monitoring endpoint returned HTTP {http_status} with malformed JSON.",
            evidence=sanitize_raw_bytes(raw_bytes, secret_values),
            last_checked_at=now,
            error_category="endpoint_error",
        )

    if (
        expected_local_id is not None
        and isinstance(payload, dict)
        and "service_id" in payload
        and payload.get("service_id") != expected_local_id
    ):
        raise ServiceIdMismatchError("External monitoring returned a different service ID.")

    evidence_secrets = secret_values
    hide_field = False
    if mapping and isinstance(payload, dict):
        found, raw_state = _lookup_nested_field(payload, mapping.status_field)
        hide_field = found and mapping.classify(raw_state) is None
        if hide_field and isinstance(raw_state, str) and raw_state:
            # Also remove copies of an opaque mapped value echoed by other fields.
            evidence_secrets = (*secret_values, raw_state)
    sanitized_payload = sanitize_value(payload, evidence_secrets)
    if isinstance(sanitized_payload, (int, float, bool)):
        sanitized_payload = str(sanitized_payload)
    if hide_field:
        parent = sanitized_payload
        *parents, leaf = mapping.status_field.split(".")
        for segment in parents:
            parent = parent.get(segment) if isinstance(parent, dict) else None
        if isinstance(parent, dict) and leaf in parent:
            parent[leaf] = "[REDACTED]"

    # 2. Apply explicit custom field mappings when configured.
    if mapping is not None:
        result = _apply_custom_mapping(
            payload,
            mapping,
            service_id=service_id,
            http_status=http_status,
            sanitized_payload=sanitized_payload,
            now=now,
        )
    else:
        # 3. Otherwise, detect recognized health response patterns.
        result = _auto_detect_health(
            payload,
            service_id=service_id,
            http_status=http_status,
            sanitized_payload=sanitized_payload,
            now=now,
        )

    # For HTTP 503, accept explicit down/degraded classification from body; reject contradictory healthy/unrecognized.
    if http_status == 503:
        if result.status in ("down", "degraded"):
            return result
        return ServiceHealth(
            service_id=service_id,
            status="unknown",
            http_status=503,
            message="Monitoring endpoint returned HTTP 503 without a consistent down/degraded health indicator.",
            reason="Monitoring endpoint error (HTTP 503)",
            requires_mapping=False,
            normalization_method="http_error",
            explanation="HTTP 503 response could not be consistently confirmed as down or degraded.",
            evidence=sanitized_payload,
            last_checked_at=now,
            error_category="endpoint_error",
        )

    return result


def _parse_plain_text_logs(
    text: str, *, service_id: str, secret_values: Sequence[str] = ()
) -> ServiceLogs:
    entries: list[LogEntry] = []
    for raw_line in text.splitlines():
        line = sanitize_text(raw_line.strip(), secret_values, 1000)
        if not line:
            continue
        ts: datetime | None = None
        level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNKNOWN"] = "UNKNOWN"
        message = line

        match = ISO_LINE_RE.match(line)
        if match:
            ts_raw = match.group("ts")
            lvl_raw = (match.group("lvl_b") or match.group("lvl") or "").lower()
            if lvl_raw in LEVEL_ALIASES:
                try:
                    parsed_ts = datetime.fromisoformat(ts_raw)
                    if parsed_ts.tzinfo is not None:
                        ts = parsed_ts
                        level = LEVEL_ALIASES[lvl_raw]
                        message = match.group("msg").strip()
                except ValueError:
                    pass
        else:
            lvl_match = LEVEL_PREFIX_RE.match(line)
            if lvl_match:
                lvl_raw = (lvl_match.group("lvl_b") or lvl_match.group("lvl") or "").lower()
                if lvl_raw in LEVEL_ALIASES:
                    level = LEVEL_ALIASES[lvl_raw]
                    message = lvl_match.group("msg").strip()

        if message:
            entries.append(LogEntry(timestamp=ts, level=level, message=message))
        if len(entries) >= MAX_LOG_LINES:
            break

    return ServiceLogs(
        service_id=service_id,
        logs=entries,
        format="text",
        available=True,
        explanation=f"Parsed {len(entries)} bounded plain-text log line(s).",
    )


def _normalize_log_item(item: Any, secret_values: Sequence[str]) -> LogEntry | None:
    if isinstance(item, str):
        text = sanitize_text(item.strip(), secret_values, 1000)
        return LogEntry(timestamp=None, level="UNKNOWN", message=text) if text else None
    if not isinstance(item, dict):
        return None
    raw_msg = item.get("message") or item.get("msg") or item.get("text")
    if not isinstance(raw_msg, str) or not raw_msg.strip():
        return None
    msg = sanitize_text(raw_msg.strip(), secret_values, 1000)

    raw_ts = item.get("timestamp") or item.get("ts") or item.get("time")
    ts: datetime | None = None
    if isinstance(raw_ts, str) and raw_ts.strip():
        try:
            parsed = datetime.fromisoformat(raw_ts.strip())
            if parsed.tzinfo is not None:
                ts = parsed
        except ValueError:
            ts = None

    raw_lvl = item.get("level") or item.get("severity")
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNKNOWN"] = "UNKNOWN"
    if isinstance(raw_lvl, str) and raw_lvl.strip().lower() in LEVEL_ALIASES:
        level = LEVEL_ALIASES[raw_lvl.strip().lower()]

    return LogEntry(timestamp=ts, level=level, message=msg)


def normalize_logs_response(
    *,
    service_id: str,
    body: bytes,
    content_type: str = "application/json",
    secret_values: Sequence[str] = (),
    expected_local_id: str | None = None,
) -> ServiceLogs:
    """Normalize compatible JSON or bounded plain-text logs without inventing fields."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return ServiceLogs(
            service_id=service_id,
            logs=[],
            format="text" if "text/" in content_type.lower() else "json",
            available=True,
            explanation="Logs endpoint returned an empty body.",
        )

    # Try JSON when body looks like JSON object/array or Content-Type explicitly declares JSON.
    is_json_candidate = text.startswith(("{", "[")) or (
        "json" in content_type.lower() and "text/" not in content_type.lower()
    )
    if is_json_candidate:
        try:
            payload = json.loads(text)
        except ValueError:
            if "text/" in content_type.lower():
                return _parse_plain_text_logs(text, service_id=service_id, secret_values=secret_values)
            raise ValueError("Invalid JSON in logs response.")

        if (
            isinstance(payload, dict)
            and expected_local_id is not None
            and "service_id" in payload
            and payload.get("service_id") != expected_local_id
        ):
            raise ServiceIdMismatchError("External monitoring returned a different service ID.")

        # Exact OpsGuard native logs contract check
        if isinstance(payload, dict) and "service_id" in payload and "logs" in payload:
            try:
                native = ServiceLogs.model_validate(
                    {"service_id": service_id, "logs": payload["logs"]}
                )
                native.logs = [
                    entry.model_copy(update={"message": sanitize_text(entry.message, secret_values, 1000)})
                    for entry in native.logs[:MAX_LOG_LINES]
                ]
                return native
            except ValidationError:
                pass

        candidates: list[Any] | None = None
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            for key in ("logs", "entries", "events", "items", "records"):
                if isinstance(payload.get(key), list):
                    candidates = payload[key]
                    break

        if candidates is not None:
            normalized_entries: list[LogEntry] = []
            for item in candidates[:MAX_LOG_LINES]:
                entry = _normalize_log_item(item, secret_values)
                if entry is not None:
                    normalized_entries.append(entry)
            if normalized_entries or not candidates:
                return ServiceLogs(
                    service_id=service_id,
                    logs=normalized_entries,
                    format="json",
                    available=True,
                    explanation=f"Normalized {len(normalized_entries)} JSON log entry/entries.",
                )

        # Unfamiliar JSON log structure: preserve bounded sanitized evidence without inventing entries.
        return ServiceLogs(
            service_id=service_id,
            logs=[],
            format="unrecognized",
            available=True,
            raw_evidence=sanitize_value(payload, secret_values),
            explanation="Logs JSON structure is unfamiliar; preserved sanitized raw evidence without inventing structured entries.",
        )

    return _parse_plain_text_logs(text, service_id=service_id, secret_values=secret_values)


def _as_non_bool_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def normalize_metrics_response(
    *,
    service_id: str,
    body: bytes,
    secret_values: Sequence[str] = (),
    expected_local_id: str | None = None,
) -> ServiceMetrics:
    """Normalize only metrics whose meaning and units are explicitly known."""
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise TypeError("Metrics response must be a JSON object.")

    if (
        expected_local_id is not None
        and "service_id" in payload
        and payload.get("service_id") != expected_local_id
    ):
        raise ServiceIdMismatchError("External monitoring returned a different service ID.")

    # Native OpsGuard contract
    if "service_id" in payload and isinstance(payload.get("metrics"), dict):
        try:
            native = ServiceMetrics.model_validate(
                {"service_id": service_id, "metrics": payload["metrics"]}
            )
            if isinstance(native.metrics, Metrics):
                return native
        except ValidationError:
            pass

    source = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else payload

    known_values: dict[str, int | float] = {}
    recognized_source_keys: set[str] = set()

    # Exact unit-explicit field mappings only (never guess units or invent missing values)
    if (v := _as_non_bool_number(source.get("error_rate_percent"))) is not None and 0 <= v <= 100:
        known_values["error_rate_percent"] = float(v)
        recognized_source_keys.add("error_rate_percent")
    elif (v := _as_non_bool_number(source.get("error_rate_pct"))) is not None and 0 <= v <= 100:
        known_values["error_rate_percent"] = float(v)
        recognized_source_keys.add("error_rate_pct")

    if (v := _as_non_bool_number(source.get("avg_latency_ms"))) is not None and isinstance(v, int) and v >= 0:
        known_values["avg_latency_ms"] = v
        recognized_source_keys.add("avg_latency_ms")
    elif (v := _as_non_bool_number(source.get("latency_ms"))) is not None and isinstance(v, int) and v >= 0:
        known_values["avg_latency_ms"] = v
        recognized_source_keys.add("latency_ms")

    if (v := _as_non_bool_number(source.get("requests_last_5m"))) is not None and isinstance(v, int) and v >= 0:
        known_values["requests_last_5m"] = v
        recognized_source_keys.add("requests_last_5m")

    if (v := _as_non_bool_number(source.get("active_db_connections"))) is not None and isinstance(v, int) and v >= 0:
        known_values["active_db_connections"] = v
        recognized_source_keys.add("active_db_connections")

    if (v := _as_non_bool_number(source.get("db_connection_limit"))) is not None and isinstance(v, int) and v >= 1:
        known_values["db_connection_limit"] = v
        recognized_source_keys.add("db_connection_limit")

    canonical_fields = (
        "error_rate_percent",
        "avg_latency_ms",
        "requests_last_5m",
        "active_db_connections",
        "db_connection_limit",
    )
    unavailable_fields = [f for f in canonical_fields if f not in known_values]
    unrecognized_fields = [
        sanitize_text(str(k), secret_values, 80)
        for k in source
        if k not in recognized_source_keys and k != "service_id"
    ]

    normalized = NormalizedMetrics(**known_values)
    if not known_values:
        return ServiceMetrics(
            service_id=service_id,
            metrics=normalized,
            available=False,
            unavailable_fields=unavailable_fields,
            unrecognized_fields=unrecognized_fields,
            raw_evidence=sanitize_value(payload, secret_values),
            explanation="No recognized metrics with known units were found in the response.",
        )

    return ServiceMetrics(
        service_id=service_id,
        metrics=normalized,
        available=True,
        unavailable_fields=unavailable_fields if unavailable_fields else None,
        unrecognized_fields=unrecognized_fields if unrecognized_fields else None,
        raw_evidence=sanitize_value(payload, secret_values) if unrecognized_fields else None,
        explanation=f"Normalized {len(known_values)} recognized metric(s) with known units.",
    )
