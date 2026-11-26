# OpsGuard AI

OpsGuard brings a service's health, recent logs and performance metrics into one incident investigation. Developers can inspect the evidence, ask a LangGraph agent for a likely cause, and approve a report for the incident history. The built-in application simulates Auth, Orders and Notifications. Registered applications supply their own monitoring data.

## Architecture

```mermaid
flowchart LR
  UI["React console · Vercel"] -->|HTTPS| API["FastAPI backend · Render"]
  API -->|Chat with gateway key| AG["LangGraph · WSO2 Agent Manager"]
  AG -->|Chat Completions| LLM["Model provider"]
  AG -->|Three read-only HTTP tools| API
  API --> DB[("SQLite on persistent disk")]
  API --> FIX["Demo fixtures and scenario state"]
  API -->|Pinned HTTPS GET| EXT["Approved external monitoring API"]
  AG -. Hosted auto-instrumentation .-> TR["WSO2 traces"]
```

The frontend uses React, TypeScript, Vite and Tailwind CSS. Both Python applications use FastAPI, Pydantic and HTTPX. The backend owns SQLite and all external monitoring credentials. The agent owns its model credential and receives monitoring data through HTTP.

```text
agent/                  Chat server, model settings, LangGraph, prompt and tools
backend/app/data/       Original Day 1 monitoring fixtures
backend/app/routes/     Monitoring endpoints and agent proxy
backend/app/            Sessions, scenarios, incident store, registry and HTTPS reader
frontend/src/           Dashboard, service details, investigator, incidents and applications
tests/                  Python regression, boundary and two-server integration tests
docs/api-contract.md    Wire formats, external endpoint examples and errors
render.yaml             Backend Blueprint with persistent disk
frontend/vercel.json    Frontend production build settings
.env.example            Server configuration template
```

## Local setup

Use Python 3.11 or later and Node 22.12+ or a compatible newer release. From this repository in PowerShell, set up the environment once. Keep an existing local configuration when present.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
npm.cmd ci --prefix frontend
```

Set `OPERATOR_PASSWORD` in the root `.env` to a strong random password. Set `LLM_API_KEY`, `LLM_MODEL` and `LLM_BASE_URL` for a provider with OpenAI-compatible Chat Completions and function calling. On Windows, run `.\start.ps1` from the repository root to start all three servers in one terminal. Press Ctrl+C to stop them together.

To start each process in its own terminal, run these commands from the repository root.

```powershell
.\.venv\Scripts\python.exe -m backend.app.main
.\.venv\Scripts\python.exe -m agent.main
npm.cmd run dev --prefix frontend -- --host localhost
```

Open the [dashboard](http://localhost:5173) and choose **Operator sign-in** for simulation, registration and incident controls. Backend Swagger runs on [port 8001](http://localhost:8001/docs) and agent Swagger on [port 8000](http://localhost:8000/docs). On Linux or macOS, create the virtual environment with `python3 -m venv .venv`, use `.venv/bin/python` and use `npm` in place of `npm.cmd`.

## Environment

Existing process variables take precedence over the root `.env`. Vite reads its own `frontend/.env` or build environment.

| Setting | Where | Default or value |
| --- | --- | --- |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | Agent | Empty key, `gpt-4o-mini`, `https://api.openai.com/v1` |
| `LLM_TIMEOUT_SECONDS` | Agent | 20 seconds per model call |
| `MONITORING_API_BASE_URL`, `MONITORING_TIMEOUT_SECONDS` | Agent | Local backend on 8001, 5 seconds per read. Set to the real Render HTTPS origin in WSO2 |
| `MONITORING_API_KEY` | Backend and agent | Shared read-only secret for protected monitoring endpoints |
| `AGENT_CHAT_URL`, `AGENT_API_KEY` | Backend | Local agent on port 8000 at `/chat`, empty key. In cloud use the full WSO2 invocation URL and endpoint key |
| `AGENT_CHAT_TIMEOUT_SECONDS` | Backend | 65 seconds. Agent deadline is 60 seconds |
| `BACKEND_HOST`, `BACKEND_PORT`, `PORT` | Backend | The example binds to loopback locally. Without a host override the server listens on all interfaces. `PORT` overrides port 8001 |
| `AGENT_PORT` | Agent | 8000 |
| `CORS_ORIGINS` | Backend | Comma-separated exact origins, default `http://localhost:5173` |
| `OPERATOR_PASSWORD` | Backend | Empty disables sign-in. Render generates a secret |
| `PROTECT_READS` | Backend | `false` enables public local reads and chat. Use `true` in hosted environments |
| `DATABASE_PATH` | Backend | Repository-relative default `.local/opsguard.sqlite3`. An explicit relative path resolves against process CWD |
| `APPROVED_HTTPS_ORIGINS` | Backend | Comma-separated origins, e.g. `https://monitor.example.com`. Empty disables integrations |
| `EXTERNAL_BEARER_TOKENS` | Backend secret | JSON map of exact external endpoint URLs to read-only bearer tokens, default `{}` |
| `VITE_API_BASE_URL` | Frontend | Monitoring backend origin, default `http://localhost:8001`. This build-time value is public |

## Investigation and reporting

The initial fixtures show Orders down with HTTP 503, a 42% error rate, and 100 of 100 database connections in use. Reset the demo to start an all-healthy walkthrough. Auth can simulate token validation failure, Orders can exhaust its connection pool, and Notifications can simulate SMTP timeouts. Reset and activation change health, logs and metrics together. “Last checked” is the time the browser successfully fetched health. Log timestamps describe the observations themselves.

The agent can call `get_service_health`, `get_service_logs` and `get_service_metrics`. The console passes the selected service ID with each investigation. The graph restricts a scoped investigation to that ID, requires a tool result, and caps the run at nine tool calls and twelve graph steps. Missing capabilities and failed reads are reported as unavailable. A successful check describes the observed endpoint.

Select **Prepare incident draft** after an investigation. The agent rechecks the selected service using the preceding answer as background. The backend validates the structured report. Review the evidence, likely cause, severity and recommendations before clicking **Confirm incident creation**. Only the authenticated confirmation endpoint writes the report. An approval UUID makes retries idempotent. The UI displays the persisted ID and lets an operator resolve or reopen the incident.

## Connect another service

1. The operator adds the canonical public HTTPS origin to `APPROVED_HTTPS_ORIGINS` and configures any endpoint-specific bearer tokens on the backend.
2. Sign in, open **Applications**, and register a name, upstream service ID and health path. Supply logs and metrics paths only if implemented.
3. Click **Validate** to fetch and validate the health payload. Select **Open dashboard** and inspect or investigate the service. OpsGuard assigns a globally unique ID while the external API continues to return its own local service ID.

The adapter checks DNS on registration and every read, rejects restricted answers, and connects to a validated public IPv4 address. It preserves the hostname for TLS and the HTTP Host header, disables proxies and redirects, requires identity encoding, and caps each read at five seconds and 64 KiB. Integrations use HTTPS port 443 and fixed paths. An external network firewall can further restrict egress to approved destinations where the hosting environment supports it. Operator-provided evidence should be scrubbed of secrets before it reaches the model provider or trace platform. [External monitoring contract](docs/api-contract.md#external-monitoring-contract)

## Deploy and verify

Start from the Git repository containing `render.yaml`. Publish the reviewed source to a branch accessible to each provider. Keep all three deployments on the same revision for this walkthrough.

1. **Render backend.** Create a Blueprint from the repository root. The supplied configuration selects a paid Starter web service and 1 GB persistent disk at `/var/data`. Build with `pip install -r backend/requirements.txt` and start with `python -m backend.app.main`. Keep the provider-assigned `PORT`. Verify `GET /healthz` returns `{"status":"ok"}`. Record the actual HTTPS origin. Retrieve the generated operator password and read-only monitoring key in Render's environment settings. Persistent SQLite requires a mounted disk. With ephemeral storage, reports, sessions, registrations and scenario state can disappear on restart or redeploy. [Render Blueprint specification](https://render.com/docs/blueprint-spec)
2. **WSO2 hosted agent.** In Agent Manager Cloud, choose a project, create a platform-hosted agent from the Git source and branch, and set the application path to the repository root. Select Python Buildpack, Python 3.12 (the documented minimum is 3.11), start command `python -m agent.main`, and **Chat Agent**. The root `requirements.txt` resolves both Python packages. The chat contract uses `POST /chat` on port 8000. OpsGuard exposes `GET /healthz` for liveness. The hosted Chat Agent guide specifies the chat route and port without mandating an HTTP health path. [WSO2 hosted agent guide](https://wso2.com/agent-platform/docs/cloud/tutorials/create-your-first-agent/)
3. **WSO2 environment and traces.** Set `LLM_API_KEY` and `MONITORING_API_KEY` as secrets. Set `LLM_MODEL`, `LLM_BASE_URL`, and `MONITORING_API_BASE_URL` to the model configuration and the Render HTTPS origin. Keep platform auto-instrumentation enabled, build and deploy. Keep endpoint API-key authentication enabled. Under **Security → Credentials**, create an invocation key. Set Render's `AGENT_CHAT_URL` to the full chat invocation URL shown for the deployed environment and `AGENT_API_KEY` to this key. The backend sends it as `X-API-Key`. [Endpoint authentication](https://wso2.com/agent-platform/docs/cloud/guides/secure-agent-endpoints-with-api-keys/) · [Hosted observability](https://wso2.com/agent-platform/docs/cloud/concepts/observability/)
4. **Vercel frontend.** Import the same repository, select `frontend` as Root Directory and Vite as framework. Use `npm ci`, `npm run build`, and output directory `dist`. Set `VITE_API_BASE_URL` to the Render HTTPS origin and deploy. Set Render's `CORS_ORIGINS` to the actual Vercel HTTPS origin. Navigation uses client state at the root URL. Rebuild the frontend after changing build-time variables. [Vite on Vercel](https://vercel.com/docs/frameworks/frontend/vite)
5. **Cloud acceptance.** Run the walkthrough below. In the WSO2 agent's **Observability → Traces**, select the deployment environment and the investigation time window. Open the request, identify the model spans and the health, logs and metrics tool calls, and compare their results with the answer. Record the actual endpoint, revision, trace ID, timestamp and saved incident ID. A green health check alone only verifies process availability.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-local
.\.venv\Scripts\python.exe -m compileall -q agent backend tests
.\.venv\Scripts\python.exe -m pip check
npm.cmd test --prefix frontend
npm.cmd run lint --prefix frontend
npm.cmd run typecheck --prefix frontend
npm.cmd run build --prefix frontend
npm.cmd ls --prefix frontend --depth=0
```

The baseline before implementation was **51 passing Python tests**. On November 24, 2026, the expanded suite passed **84 Python tests and 4 frontend tests**. Python compilation, dependency checks, frontend linting, TypeScript and the Vite production build passed. Headless Chrome exercised the production bundle against a disposable local backend, including sign-in, reset, Orders failure, the unavailable-agent error, recovery and a 390-pixel viewport.

The two-server HTTP test ran the real FastAPI processes, LangGraph, HTTP tools and SQLite against a scripted OpenAI-compatible model. It checked the Orders evidence and saved incident ID. A separate test exercised a registered external service through the graph with health and logs available and metrics unavailable. Live provider responses, third-party monitoring endpoints, hosted deployments and WSO2 traces still require credentialed verification.

## Four-minute demonstration

| Time | Action |
| --- | --- |
| 0:00–0:45 | Open the dashboard, sign in, select Built-in demo and reset all services. Show three healthy services. |
| 0:45–1:20 | Open Order Service details and simulate failure. Show HTTP 503, the pool timeout log, elevated latency, 42% errors and 100/100 connections. |
| 1:20–2:10 | Click Investigate and ask “Why is my order service failing?” With a configured provider, review the observations and the agent's likely cause. |
| 2:10–3:00 | Prepare the incident draft and review the evidence. Click Confirm, show the returned incident ID, and open its history entry. |
| 3:00–3:30 | Recover Orders and refresh its details. Show HTTP 200 and the recovered metrics. Resolve the report if appropriate. |
| 3:30–4:00 | In a deployed environment, open the WSO2 trace and expand the model and tool spans. |

## MVP limitations and next steps

This is a single-operator workspace with shared demo state. Everyone with an operator session can change that workspace. Sign-in issues a one-hour bearer token kept in browser memory. The agent has read-only tools, and the console chat transcript lasts only within the current service context. An LLM can still misinterpret evidence. Review the report before approving it. External integrations support one service per registration and require compatible JSON. The SQLite design targets a single disk-backed backend instance. For a larger deployment, add per-user roles, login and model-usage rate limits, and database backups.
