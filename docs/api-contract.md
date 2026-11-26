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

`GET /api/services/{id}/health` takes a service ID in the path. A successful lookup returns HTTP 200. The `http_status` field records the service status code reported by the monitoring source.

```json
{
  "service_id": "orders",
  "status": "down",
  "http_status": 503,
  "message": "Order processing is unavailable"
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

`POST /api/chat` sends the request to the agent with an empty context object by default. Include an optional `service_id` to forward the selected service in `context.service_id`. On success, it echoes the session ID.

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
| `GET /api/applications` | None | Array with demo and registered applications, IDs, service_ids and endpoint configuration |
| `POST /api/applications` | Registration JSON below | 201 with generated application ID and namespaced service ID. Duplicate origin and upstream service ID returns 409 |
| `POST /api/applications/{id}/validate` | No body | Normalized current health payload from the external endpoint |
| `DELETE /api/applications/{id}` | No body | 204. Built-in demo deletion returns 409 |

```json
{
  "name":"Library API","base_url":"https://monitor.example.com","service_id":"library-api",
  "health_path":"/health","logs_path":"/logs","metrics_path":"/metrics"
}
```

The sample hostname is illustrative. The operator must explicitly allow the real origin and configure compatible endpoints first. The service ID returned in `service_ids` is the one to use with monitoring routes, chat and incidents. The catalog includes the last observed external health status or `unknown` until checked or following a failed read. Fetch `/health` for the current observation. Logs and metrics paths may be omitted or null. External endpoint tokens are configured on the server, scoped to exact origin and path.

## External monitoring contract

Return HTTP 200 with an uncompressed JSON body describing the observation, including when reporting that a monitored service is down. Use the registered upstream service ID; the backend substitutes the namespaced identifier when forwarding. Provide timezone-aware timestamps and a health status of healthy, degraded or down. Each endpoint is read with GET. The response bodies below are three separate examples.

```json
{"service_id":"library-api","status":"healthy","http_status":200,"message":"Catalog query completed"}
```

```json
{"service_id":"library-api","logs":[{"timestamp":"2026-11-24T10:00:00Z","level":"INFO","message":"Catalog query succeeded"}]}
```

```json
{"service_id":"library-api","metrics":{"error_rate_percent":0.1,"avg_latency_ms":40,"requests_last_5m":300}}
```

Logs support DEBUG, INFO, WARNING, ERROR and CRITICAL. The backend sorts them newest first and applies the requested limit. Metrics require a percentage from 0 to 100, nonnegative integer latency and request count. Database connection counts are optional. Keep each response within 64 KiB. A missing configured capability yields 501; timeout yields 504; upstream errors, redirects, oversized bodies, mismatched IDs and malformed JSON yield 502. Restricted network destinations or a removed origin approval yield 403. Only approved DNS hostnames on HTTPS port 443 with a public IPv4 address are supported.
