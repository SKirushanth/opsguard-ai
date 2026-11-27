param location string = resourceGroup().location
param registryName string
@minLength(1)
param imageTag string
@secure()
@minLength(16)
param operatorPassword string
param approvedOrigins string = ''
@secure()
param externalBearerTokens string = ''
// Activate the hosted agent later by supplying its HTTPS endpoint and keys.
param agentChatUrl string = 'http://127.0.0.1:8000/chat'
@secure()
param agentApiKey string = ''
@secure()
param monitoringApiKey string = ''

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = { name: registryName }
resource env 'Microsoft.App/managedEnvironments@2024-03-01' existing = { name: 'opsguard-env' }
resource pull 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = { name: 'opsguard-pull' }
var identity = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${pull.id}': {} }
}
var registryAuth = [{ server: registry.properties.loginServer, identity: pull.id }]
var frontendOrigin = 'https://opsguard-web.${env.properties.defaultDomain}'
var probeTypes = ['Startup', 'Readiness', 'Liveness']

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'opsguard-api'
  location: location
  identity: identity
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registryAuth
      ingress: { external: false, targetPort: 8001, allowInsecure: false }
      secrets: concat([
        { name: 'operator-password', value: operatorPassword }
        { name: 'external-tokens', value: empty(externalBearerTokens) ? '{}' : externalBearerTokens }
      ], empty(agentApiKey) ? [] : [{ name: 'agent-key', value: agentApiKey }],
         empty(monitoringApiKey) ? [] : [{ name: 'monitoring-key', value: monitoringApiKey }])
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 1 }
      volumes: [{
        name: 'data'
        storageType: 'AzureFile'
        storageName: 'opsguard-data'
        mountOptions: 'uid=10001,gid=10001,file_mode=0600,dir_mode=0700'
      }]
      containers: [{
        name: 'backend'
        image: '${registry.properties.loginServer}/opsguard/backend:${imageTag}'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        volumeMounts: [{ volumeName: 'data', mountPath: '/data' }]
        env: concat([
          { name: 'PROTECT_READS', value: 'true' }
          { name: 'OPERATOR_PASSWORD', secretRef: 'operator-password' }
          { name: 'CORS_ORIGINS', value: frontendOrigin }
          { name: 'APPROVED_HTTPS_ORIGINS', value: approvedOrigins }
          { name: 'EXTERNAL_BEARER_TOKENS', secretRef: 'external-tokens' }
          { name: 'AGENT_CHAT_URL', value: agentChatUrl }
        ], empty(agentApiKey) ? [] : [{ name: 'AGENT_API_KEY', secretRef: 'agent-key' }],
           empty(monitoringApiKey) ? [] : [{ name: 'MONITORING_API_KEY', secretRef: 'monitoring-key' }])
        probes: [for kind in probeTypes: {
          type: kind
          httpGet: { path: kind == 'Readiness' ? '/readyz' : '/healthz', port: 8001 }
          periodSeconds: 10
          timeoutSeconds: 5
          failureThreshold: kind == 'Startup' ? 12 : 3
        }]
      }]
    }
  }
}
resource frontend 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'opsguard-web'
  location: location
  identity: identity
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: registryAuth
      ingress: { external: true, targetPort: 8080, allowInsecure: false }
    }
    template: {
      scale: { minReplicas: 1, maxReplicas: 2 }
      containers: [{
        name: 'frontend'
        image: '${registry.properties.loginServer}/opsguard/frontend:${imageTag}'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [{ name: 'BACKEND_ORIGIN', value: 'https://${backend.properties.configuration.ingress.fqdn}' }]
        probes: [for kind in probeTypes: {
          type: kind
          httpGet: { path: '/healthz', port: 8080 }
          periodSeconds: 10
          failureThreshold: kind == 'Startup' ? 12 : 3
        }]
      }]
    }
  }
}
output dashboard string = 'https://${frontend.properties.configuration.ingress.fqdn}'
