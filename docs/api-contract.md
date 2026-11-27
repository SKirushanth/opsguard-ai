# OpsGuard AI API contract

The monitoring backend listens on `http://localhost:8001`. The agent listens on `http://localhost:8000`. Both provide Swagger UI at `/docs` and OpenAPI JSON at `/openapi.json`.

## Application health

`GET /healthz` on either app takes no parameters. It reports process liveness independently of upstream services or provider credentials.

```json
{"status": "ok"}
```

## Monitoring

### List services

`GET /api/services` takes no parameters.

```json
{
  "services": [
    {"id": "auth", "name": "Authentication Service", "status": "healthy"},
    {"id": "orders", "name": "Order Service", "status": "down"},
    {"id": "notifications", "name": "Notification Service", "status": "healthy"}
  ]
}
```

### Service health

`GET /api/services/{id}/health` takes a service ID in the path. A successful lookup returns HTTP 200. `status` is deterministically normalized to `healthy`, `degraded`, `down`, or `unknown`. `http_status` records the upstream HTTP status code (or `null` on connectivity failure), alongside `normalization_method`, `requires_mapping`, `reason`, `explanation`, sanitized `evidence`, `error_category`, and `last_checked_at`.

```json
{
  "service_id": "orders",
  "status": "down",
  "http_status": 503,
  "message": "Order processing is unavailable",
  "reason": null,
  "requires_mapping": false,
  "normalization_method": "simulated",
  "explanation": "Order processing is unavailable"
}
```

When an external health response is unrecognized and has no matching custom mapping, the endpoint preserves:

```json
{
  "service_id": "ext-1234-erp",
  "status": "unknown",
  "http_status": 200,
  "reason": "Unrecognized health response format",
  "requires_mapping": true,
  "normalization_method": "unrecognized"
}
```

### Service logs

`GET /api/services/{id}/logs` takes a service ID and an optional integer `limit` query parameter from 1 to 100. The default is 20. Entries appear newest first, up to the number available.

The response below is for `/api/services/orders/logs?limit=1`.

```json
{
  "service_id": "orders",
  "logs": [
    {
      "timestamp": "2026-11-23T10:00:19Z",
      "level": "ERROR",
      "message": "HTTP 503 returned for POST /orders because the database connection was unavailable"
    }
  ]
}
```

### Service metrics

`GET /api/services/{id}/metrics` takes a service ID. Database metrics are included when present in the fixture.

```json
{
  "service_id": "orders",
  "metrics": {
    "error_rate_percent": 42.0,
    "avg_latency_ms": 2400,
    "requests_last_5m": 120,
    "active_db_connections": 100,
    "db_connection_limit": 100
  }
}
```

## Chat

Chat requests use JSON and require a `message` of 1–4000 characters and a `session_id` of 1–128 characters after trimming whitespace. Chat responses return HTTP 200 when an investigation, clarification, or monitoring availability message is produced. Each chat request starts a new investigation.

### Frontend gateway

`POST /api/chat` requests monitoring references from the agent. Include an optional `service_id` to forward the selected service in `context.service_id`. On success, it echoes the session ID.

```json
{"message": "Why is the order service failing?", "session_id": "test-001"}
```

```json
{"response": "Investigation results…", "session_id": "test-001"}
```

### Agent

`POST /chat` is the internal agent interface. `context` is an optional JSON object that defaults to `{}` and accepts an optional `service_id` for a scoped investigation.

```json
{"message": "Why is the order service failing?", "session_id": "test-001", "context": {}}
```

```json
{"response": "Investigation results…"}
```

For backend requests, `context.response_format` is `monitoring-references-v1`. The agent uses the same `response` string to return JSON containing a service ID, the resource names read, and an optional selection of observation IDs and supported hypotheses. The gateway checks the service scope, reads those resources again and renders the answer from backend evidence. A log reference identifies its content and timestamp, so a changed log cannot silently support an earlier hypothesis. An invalid selection falls back to observations. An incompatible agent envelope yields 502. Deploy the backend and agent from the same revision.

Direct callers can omit this internal format to receive the usual human-readable response. The frontend chat and incident schemas stay the same. Incident drafts use the same evidence rules and still require explicit operator confirmation before storage.

## Errors

Application errors use a JSON object with a string `detail`. FastAPI validation errors use a list of field errors in `detail`.

| HTTP status | Endpoint | Meaning |
| --- | --- | --- |
| 404 | Monitoring service resource | Unknown service ID |
| 422 | Logs and chat | Invalid query parameter or request body |
| 502 | `/api/chat` | Agent returned an invalid response or unexpected error |
| 502 | `/chat` | Provider rejected the request, the model response could not be used, or investigation steps were exhausted |
| 503 | Monitoring service endpoint | Fixture missing, malformed, or incomplete |
| 503 | `/api/chat` | Agent unreachable or unavailable |
| 503 | `/chat` | Missing key or provider credentials, permissions, or quota unavailable |
| 504 | Chat | Agent request or investigation timed out |

```json
{"detail": "Service was not found."}
```

```json
{
  "detail": [
    {
      "type": "greater_than_equal",
      "loc": ["query", "limit"],
      "msg": "Input should be greater than or equal to 1"
    }
  ]
}
```

## Manual requests

```bash
curl -fsS http://localhost:8001/healthz
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8001/api/services
curl -fsS http://localhost:8001/api/services/orders/health
curl -fsS 'http://localhost:8001/api/services/orders/logs?limit=20'
curl -fsS http://localhost:8001/api/services/orders/metrics

curl -fsS http://localhost:8001/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Why is the order service failing?","session_id":"test-001"}'

curl -fsS http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Check the authentication service.","session_id":"test-002","context":{}}'
```

## Access and sessions

The local default exposes reads and chat. Set `PROTECT_READS=true` to require a bearer session on all workspace endpoints. Only the three monitoring resources and service catalog also accept the server-only `X-Monitoring-Key`. The agent gateway uses its own `X-API-Key`. The public `/healthz`, `/docs` and `/openapi.json` endpoints contain no workspace observations.

| Method and path | Request | Response |
| --- | --- | --- |
| `POST /api/session` | `{"password":"<operator password>"}` | `{"token":"<temporary bearer>","expires_in":3600}`. No-store response |
| `DELETE /api/session` | Bearer session | 204, revokes the current session |

Send `Authorization: Bearer <token>` for every write and incident draft. Invalid or expired credentials produce 401. A missing server-side operator password produces 503 at sign-in. The backend stores only the token digest. Use HTTPS in hosted environments.

## Demo simulation

| Method and path | JSON request | Result |
| --- | --- | --- |
| `POST /api/demo/scenario` | `{"service_id":"orders","scenario":"database_pool_exhausted"}` | 200 with service_id, scenario and status |
| `POST /api/demo/reset` | `{"service_id":"orders"}` or `{}` for all | 200 with service_ids and status healthy |

| Service ID | Scenario | Evidence |
| --- | --- | --- |
| auth | token_validation_failure | Down, HTTP 401, token validation errors |
| orders | database_pool_exhausted | Down, HTTP 503, pool timeouts, 100/100 connections |
| notifications | smtp_timeout | Degraded, HTTP 504, email delivery timeout, elevated latency |

Only the three demo IDs are accepted. The initial fixture state remains in effect until an operator activates or resets a scenario. Reset writes a healthy snapshot. Unsupported combinations produce 422.

## Incidents

| Method and path | Request | Result |
| --- | --- | --- |
| `POST /api/incidents/draft` | application_id, service_id, session_id and preceding investigation text (1–4000 characters) | Validated draft, no write. Invalid model output produces 502 |
| `POST /api/incidents` | Draft fields plus boolean confirmed and UUID approval_id | 201 with durable report, 200 for identical retry, 409 for reused key with different draft |
| `GET /api/incidents` | Optional application_id query parameter | Array ordered newest first. Includes reports for removed applications |
| `PATCH /api/incidents/{id}` | `{"status":"resolved"}` or `{"status":"open"}` | Updated report. Resolution sets a UTC timestamp, reopening clears it |

Example confirmation request, made only after reviewing the draft in the console.

```json
{
  "application_id":"demo", "service_id":"orders", "title":"Order database timeout",
  "description":"Order processing is unavailable.", "severity":"critical",
  "evidence":["HTTP 503","100 of 100 connections active"],
  "likely_root_cause":"Database connection pool exhaustion",
  "recommendations":["Inspect connection ownership","Check for connection leaks"],
  "approval_id":"87decb89-50ff-473c-acb1-e191e1843078", "confirmed":true
}
```

Severity accepts low, medium, high or critical. Creation validates application ownership, nonempty evidence and recommendations, a real boolean confirmation, and operator authorization. Saved responses include the draft fields and `id`, `created_at`, `status` and nullable `resolved_at`. Approvals use an authenticated backend write. The LLM has no incident-write tool.

## Applications

| Method and path | Request | Result |
| --- | --- | --- |
| `GET /api/applications` | None | Array with demo and registered applications, IDs, service_ids, endpoint configuration, and optional `health_mapping` |
| `POST /api/applications/test-connection` | Connection probe JSON (`base_url`, `health_path`, optional `service_id`, `logs_path`, `metrics_path`, `health_mapping`) | Non-persisting, non-LLM probe returning connection result, HTTP status, sanitized response, normalized status, `requires_mapping`, and explanation |
| `POST /api/applications/validate-mapping` | `mapping` (`CustomHealthMapping`) and optional `sample_response` / `http_status` | Deterministic mapping validation and live classification preview (422 on invalid paths or conflicting values) |
| `POST /api/applications` | Registration JSON below (with optional `health_mapping`) | 201 with generated application ID and namespaced service ID. Duplicate origin and upstream service ID returns 409 |
| `PUT /api/applications/{id}/mapping` | `{"health_mapping": ...}` or `{"health_mapping": null}` | Updates custom response mapping in SQLite (409 for demo) |
| `POST /api/applications/{id}/validate` | No body | Strict normalized health check from the external endpoint (502 on upstream HTTP/format/mapping errors) |
| `DELETE /api/applications/{id}` | No body | 204. Built-in demo deletion returns 409 |

```json
{
  "name": "Library API",
  "base_url": "https://monitor.example.com",
  "service_id": "library-api",
  "health_path": "/health",
  "logs_path": "/logs",
  "metrics_path": "/metrics",
  "health_mapping": {
    "status_field": "application.currentState",
    "healthy_values": ["ACTIVE"],
    "down_values": ["INACTIVE"],
    "degraded_values": ["LIMITED"]
  }
}
```

The sample hostname is illustrative. The operator must explicitly allow the real origin first. The service ID returned in `service_ids` is the one to use with monitoring routes, chat and incidents. The catalog includes the last observed external health status or `unknown` until checked or following a failed/unrecognized read. Logs and metrics paths may be omitted or null. External endpoint tokens are configured on the server, scoped to exact origin and path.

## External monitoring contract

The Universal Monitoring Adapter normalizes health responses in strict deterministic order:

1. **HTTP & network errors:** Connectivity failures, HTTP 401/403 authentication failures, blocked 3xx redirects, malformed JSON, and non-503 error codes normalize to `unknown` with distinct `error_category` (`connectivity_failure`, `authentication_failure`, `endpoint_error`). HTTP 503 with consistent `DOWN`/`DEGRADED` JSON normalizes to `down`/`degraded`.
2. **Custom mapping:** When `health_mapping` is configured, evaluates the nested dot-separated `status_field` for string or boolean values without falling back to automatic detection on missing or unmapped values. Explanations omit the upstream value. The preview redacts an unmatched mapped field and copies of its opaque string value.
3. **Automatic detection:** Supports native OpsGuard JSON, standard status strings (`UP`, `OK`, `HEALTHY`, `DEGRADED`, `WARN`, `DOWN`, `OUT_OF_SERVICE`, `ERROR`, `FAIL`), boolean `{"healthy": true|false}`, and consistent nested objects (`components`, `health`, `data`, `result`, `checks`). Traversal follows recognized wrappers and component maps for at most eight nested levels and 128 health objects. Conflicting indicators, unrecognized component states and exceeded limits yield `unknown` with `requires_mapping: true`. A down aggregate may include healthy dependencies. Ordinary business objects are outside this traversal.
4. **Unrecognized fallback:** Returns `status: "unknown"`, `reason: "Unrecognized health response format"`, `requires_mapping: true`.

For logs, OpsGuard accepts compatible JSON log arrays/objects and bounded plain-text logs (up to 64 KiB / 100 lines), preserving bounded secret-redacted `raw_evidence` when JSON log structures are unfamiliar. For metrics, OpsGuard normalizes only recognized fields with known units (`error_rate_percent`, `avg_latency_ms`, `requests_last_5m`, `active_db_connections`, `db_connection_limit`) without inventing missing values. Missing capabilities return HTTP 501; upstream logs/metrics errors yield 502; timeouts yield 504; restricted network destinations or removed origin approvals yield 403. Only approved DNS hostnames on HTTPS port 443 with a public IPv4 address are supported.
