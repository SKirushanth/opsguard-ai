# OpsGuard on Azure Container Apps

The browser uses one HTTPS origin. Nginx serves React and proxies `/api/` to the private FastAPI app using its internal HTTPS FQDN, certificate verification and runtime DNS resolution. Both images run as non-root users. Azure Container Registry (ACR) stores the images. Only the backend mounts SQLite storage. Monitoring, registration, mappings, simulation, operator login and manual incident operations work with the agent offline. The optional LangGraph service belongs in WSO2 Agent Manager.

## Local containers

From the directory containing `compose.yaml`, start Docker Desktop in Linux-container mode and run:

```powershell
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
Invoke-RestMethod http://localhost:8080/healthz
Invoke-RestMethod http://localhost:8001/readyz
```

Open **http://localhost:8080**. Compose binds both published ports to loopback. Guest reads work on the local defaults. To use operator controls, set `OPERATOR_PASSWORD` in the root `.env` and run `docker compose up -d` again. The Compose file selects individual backend variables. Host Python, Node, a frontend environment file and model credentials are optional for this workflow.

| Setting | Where it applies |
| --- | --- |
| `FRONTEND_HOST_PORT`, `BACKEND_HOST_PORT` | Optional host port overrides, default 8080 and 8001. Container target ports stay fixed. |
| `BACKEND_HOST`, `BACKEND_PORT`, `PORT` | Backend image listens on 0.0.0.0:8001. A standalone backend can override its port with `PORT` or `BACKEND_PORT`; align the proxy and published port. |
| `DATABASE_PATH` | Image default /data/opsguard.sqlite3. Compose mounts the named volume monitoring-data at /data. |
| `BACKEND_ORIGIN` | Nginx runtime setting. Supply a trusted scheme and host with optional port and no trailing slash. Compose sets http://backend:8001. Azure supplies the private HTTPS FQDN. |
| `VITE_API_BASE_URL` | Container build sets / for same-origin requests. The native Vite development workflow retains its existing backend URL. |
| `OPERATOR_PASSWORD`, `PROTECT_READS` | Operator login and access policy. Azure requires a password and enables protected reads. |
| `CORS_ORIGINS` | Exact browser origins for direct cross-origin API clients. The Azure template sets the frontend HTTPS origin. |
| `APPROVED_HTTPS_ORIGINS`, `EXTERNAL_BEARER_TOKENS` | Backend-only allowlist and optional JSON object of exact endpoint URLs to read-only credentials. Empty approval disables external registration. |
| `AGENT_CHAT_URL`, `AGENT_API_KEY`, `AGENT_CHAT_TIMEOUT_SECONDS` | Backend settings for future agent invocation. Default timeout is 65 seconds. |
| `MONITORING_API_KEY` | Optional shared read-only key for the agent to fetch protected monitoring data. |

Use `docker compose logs --tail 100 backend frontend` for startup failures. Backend readiness checks SQLite initialization and reads the registry. Backend liveness and frontend health use /healthz. If a port is occupied, change the corresponding host port. A 401 with protected reads requires operator sign-in. A 503 on chat with monitoring still available is expected when the agent is offline. Registrations require a public HTTPS origin on the allowlist. Use the existing API contract for manual confirmed incident creation. The dashboard supports reviewing, resolving and reopening incidents; its guided draft flow uses the agent. [API contract](api-contract.md)

Stop with `docker compose down`. Restart under the same Compose project name to reuse the volume. Adding `--volumes` deletes its data. The native `.local/opsguard.sqlite3` and the Compose volume are separate workspaces.

## Future Azure deployment from PowerShell

These commands create billable resources when executed. Use a dedicated resource group, Azure CLI, Bicep, Docker with Linux amd64 builds, a subscription with the relevant resource providers registered, and permissions to deploy resources and assign roles. The operator pushing images needs ACR push permission. The templates give the apps a registry-scoped AcrPull managed identity and disable registry admin credentials. [Managed identity image pulls](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity-image-pull)

**1. Create the foundation.** Choose the subscription and a region supporting Container Apps. Run from the repository root.

```powershell
az login
az account set --subscription "<subscription-id>"
$rg = "opsguard-mvp"
$region = "eastus"
az group create --name $rg --location $region
az deployment group create -g $rg -n opsguard-foundation --template-file deploy/azure/foundation.bicep --output none
$acr = az deployment group show -g $rg -n opsguard-foundation --query properties.outputs.registryName.value -o tsv
$registryHost = az deployment group show -g $rg -n opsguard-foundation --query properties.outputs.registryHost.value -o tsv
```

The foundation creates ACR, the pull identity and role, a Container Apps environment, Log Analytics and a dedicated Azure Files SMB share. Azure resolves the storage account key inside the template. Store the resource group's database backups separately.

**2. Publish reviewed images.** Choose a unique tag for the exact tested source, including any local edits. Keep that tag immutable for rollback.

```powershell
$tag = Read-Host "Unique release tag"
az acr login --name $acr
docker buildx build --platform linux/amd64 --push -f backend/Dockerfile -t "${registryHost}/opsguard/backend:${tag}" .
docker buildx build --platform linux/amd64 --push -f frontend/Dockerfile -t "${registryHost}/opsguard/frontend:${tag}" frontend
```

**3. Create the apps.** Use the Azure portal's **Deploy a custom template** with the compiled apps template or use the Az PowerShell module to pass a SecureString directly. For the portal, compile with `az bicep build --file deploy/azure/apps.bicep` and select the resulting apps.json. For PowerShell:

```powershell
Connect-AzAccount -Subscription (az account show --query id -o tsv)
$settings = @{
    registryName = $acr
    imageTag = $tag
    operatorPassword = Read-Host "Operator password (at least 16 characters)" -AsSecureString
}
New-AzResourceGroupDeployment -ResourceGroupName $rg -TemplateFile deploy/azure/apps.bicep -TemplateParameterObject $settings
$web = az containerapp show -g $rg -n opsguard-web --query properties.configuration.ingress.fqdn -o tsv
Invoke-RestMethod "https://$web/healthz"
```

Az PowerShell needs a discoverable Bicep CLI for direct .bicep deployment. Alternatively pass the compiled apps.json to `-TemplateFile`. Set `approvedOrigins` to enable specific external services. Pass optional `externalBearerTokens` as a secure parameter. Store credentials in your secret manager. The app configuration maps them to Container Apps secret references. Reuse your full parameter set on upgrades.

**4. Verify the deployment.** Open the frontend, confirm an anonymous request to /api/services returns 401, sign in, exercise a demo scenario, test an approved service, save a confirmed incident and check persistence after a backend revision change. Azure terminates ingress TLS. The public frontend listens on target port 8080 and the private backend on 8001. Startup and liveness use HTTP /healthz. Backend readiness uses /readyz. The backend gets 0.5 CPU and 1 GiB, with one replica. The frontend gets 0.25 CPU and 0.5 GiB, with one to two replicas. Check console and system logs in Container Apps or Log Analytics. [Health probes](https://learn.microsoft.com/en-us/azure/container-apps/health-probes)

```powershell
az containerapp logs show -g $rg -n opsguard-api --type system --tail 50
az containerapp logs show -g $rg -n opsguard-web --type console --tail 50
```

## SQLite operating limits

Docker uses a local named volume. Azure's container filesystem is ephemeral. The template mounts an SMB share at /data with UID/GID 10001 and restricts file and directory modes. Schema initialization creates missing tables and preserves existing records. Applications, mappings, sessions, simulation overrides and incidents share one database. [Container Apps storage mounts](https://learn.microsoft.com/en-us/azure/container-apps/storage-mounts)

**Treat Azure Files with SQLite as an MVP requiring storage qualification.** SQLite depends on reliable byte-range locking and flush semantics. A single replica reduces contention, but rolling revisions can overlap. Use a maintenance window, quiesce writes, take a consistent offline backup and stop the old writer before starting a replacement. Verify locking, permissions, restart recovery and a restore on the target share before accepting durable production traffic. Preserve byte-range locking and the existing rollback journal. For larger workloads or multi-replica writes, plan a migration to a server database such as PostgreSQL. [SQLite network filesystem guidance](https://sqlite.org/useovernet.html)

## Activate AI later

Deploy the existing agent from the same revision through WSO2 Agent Manager. Configure its `MONITORING_API_BASE_URL=https://<frontend-fqdn>`: the agent appends /api/services and Nginx forwards its read-only monitoring header. Give the backend and agent the same `MONITORING_API_KEY`. On the agent only, set `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` and optionally `LLM_TIMEOUT_SECONDS`. For Groq, use its OpenAI-compatible base URL and a currently available tool-calling model. Set the Bicep parameters `agentChatUrl` to the WSO2 HTTPS invocation URL, `agentApiKey` to its invocation secret, and `monitoringApiKey` to the shared monitoring secret. Verify an actual investigation and hosted trace after activation. [WSO2 settings and verification](../README.md#deploy-and-verify)

For Groq, the agent's `LLM_BASE_URL` is `https://api.groq.com/openai/v1` and `LLM_API_KEY` holds the Groq credential. Confirm the chosen model supports local function calling when enabling the integration. [Groq compatibility](https://console.groq.com/docs/openai) · [Tool support](https://console.groq.com/docs/tool-use/overview)

## Cleanup and cost

ACR, provisioned replicas, storage and log ingestion can incur charges while the services are idle. Export and verify backups before deleting the dedicated resource group. Review the group contents in the portal, then use `az group delete --name $rg` when ready to confirm deletion.
