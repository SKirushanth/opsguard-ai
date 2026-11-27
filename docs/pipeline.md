# Production pipeline

The release workflow tests pull requests and main pushes. Python installs the existing nested requirements (no Python lockfile exists). Node uses npm ci and the package lock. The frontend build includes TypeScript. Only successful main runs affecting application inputs can enter production. Documentation-only pushes run CI. Actions → OpsGuard CI and release → Run workflow on main reconciles deployed versions, including after a failed or skipped release. This also verifies unchanged production without rebuilding. Production runs share one non-cancelling concurrency group.

## Authentication

The dedicated user-assigned identity opsguard-github-deploy uses issuer https://token.actions.githubusercontent.com, audience api://AzureADTokenExchange and subject repo:SKirushanth@216712154/opsguard-ai@1468813984:environment:production. GitHub restricts the production environment to the main branch. Keep workflow edits and pushes to main restricted to trusted maintainers. Repository variables AZURE_CLIENT_ID, AZURE_TENANT_ID and AZURE_SUBSCRIPTION_ID are identifiers. WSO2_VERIFIED_SHA records the Git commit last attested by the agent operator. The production environment secret OPERATOR_PASSWORD must match the current workspace password for read-only record counts and a short-lived operator smoke session. Update that environment secret when rotating the password. No Azure client secret, registry password, Groq key or WSO2 key belongs in GitHub.

Recovery setup: create the dedicated managed identity in opsguard-rg, add the federated credential above, and assign AcrPush at opsguardacr39424 only. Assign a custom role with Microsoft.App/containerApps/read, Microsoft.App/containerApps/write and Microsoft.App/containerApps/revisions/read at each of opsguard-api and opsguard-web individually. Configure the three identifiers from the identity and Azure account. Production uses no resource-group deployment rights.

## Release and data

The pipeline reads the live healthy Single-mode templates, checks ingress, traffic, mounts, backend scaling and protected reads, and records the current image digests in the run summary. It compares each live component's source with main (including shared inputs), then publishes only affected Linux AMD64 images named for the full Git SHA. Retries reuse published SHA tags. Keep release tags immutable in practice; do not retag them. The initial demo-20261127t130811 images map to commit 7daf9eaf02921d5579564ff9928ae0a7dad5eaee. An unknown baseline fails closed.

Backend replacement invalidates sessions and clears container-local SQLite, including scenario overrides. The workflow refuses replacement while external registrations or incidents exist. Coordinate a quiet deployment window: the read-only check cannot prevent a concurrent operator write. Preserve required records separately before authorizing disposal. Rolling back an image cannot restore data. Do not run the legacy infrastructure templates against this environment.

Backend deploys first. A ready revision must serve 100% traffic with the expected image. Disposable demo sessions exercise analysis, incident approval/resolution, reset and isolation. Browser verification checks automatic initialization, Service Details and Demo analysis. One authenticated operator health investigation checks reference-backed rendering. Provider unavailability is reported as a warning on unrelated releases; deterministic demo checks must always pass.

## Hosted agent checkpoint

For agent, shared evidence contract or agent dependency changes, build and deploy the intended commit in the existing WSO2 Agent Manager project from the repository root, Python 3.12, start command python -m agent.main. Retain existing environment and invocation authentication. Verify the built commit in the console. Invoke POST /chat for orders with a fresh session_id and context containing service_id orders and response_format monitoring-references-v1. Parse the response string as JSON; require service_id orders and resources containing health. Confirm the monitoring tool span and successful HTTP health read in the hosted trace. Only then set the repository variable WSO2_VERIFIED_SHA to that full commit and dispatch the workflow. Agent-only changes with no Azure component change need no container release. There is no stored WSO2 management credential. Contract-dependent backend releases require the checkpoint and a successful live check. Unrelated releases can continue during a trial outage.

## Rollback and troubleshooting

On regression, the workflow attempts restoration in reverse activation order, using the recorded digests in fresh Single-mode revisions, and tests the restored demo. It leaves the run failed for investigation. The offline rollback test injects a failure after both activations and checks reverse restoration. No production failure is injected.

For manual restoration, identify the baseline revision and digest from the failed run summary. Inspect the revision for correct ingress configuration, environment, probes and absence of mounts; run the operator record check before any backend replacement. Use Azure CLI:

    az containerapp revision copy -g opsguard-rg -n APP --from-revision KNOWN_GOOD --image REGISTRY/REPO@sha256:DIGEST --revision-suffix rb-1234-FULL_BASELINE_SHA --output none
    az containerapp revision list -g opsguard-rg -n APP --query "[?properties.active].{revision:name,health:properties.healthState,traffic:properties.trafficWeight}" -o table

Confirm Healthy at 100% and dispatch the verification workflow after correcting the main branch (dispatch reconciles to main). Never set traffic weights in Single mode. If OIDC fails, compare the exact subject, environment branch rule, tenant and client ID. For registry or ARM 403s check resource-level role assignments and propagation. For a blocked agent release inspect the hosted trace and update the attestation only after testing. For a failed readiness check inspect Container Apps system logs. Keep credentials and unredacted application logs out of Actions outputs.
