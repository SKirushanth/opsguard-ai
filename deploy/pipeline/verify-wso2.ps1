param(
    [Parameter(Mandatory = $true)]
    [uri] $AgentChatUrl
)

$ErrorActionPreference = "Stop"
if ($AgentChatUrl.Scheme -ne "https" -or -not $AgentChatUrl.IsAbsoluteUri -or
    $AgentChatUrl.UserInfo -or $AgentChatUrl.Query -or $AgentChatUrl.Fragment) {
    throw "Pass the HTTPS WSO2 Chat invocation URL without credentials, query, or fragment."
}

$key = Read-Host "WSO2 invocation API key" -AsSecureString
$session = "release-check-" + [guid]::NewGuid().ToString("N")
$body = @{
    message = "Use the Orders logs and metrics to explain the likely cause and recommend what to check next."
    session_id = $session
    context = @{
        service_id = "orders"
        response_format = "monitoring-references-v1"
        answer_format = "conversational-v1"
        previous_questions = @("What does the Orders health check report?")
    }
} | ConvertTo-Json -Depth 5 -Compress

Write-Host "One invocation at $([DateTimeOffset]::UtcNow.ToString('o')), session $session"
$credential = [pscredential]::new("wso2", $key)
try {
    $reply = Invoke-RestMethod -Uri $AgentChatUrl -Method Post -ContentType "application/json" `
        -Headers @{ "X-API-Key" = $credential.GetNetworkCredential().Password } `
        -Body $body -MaximumRedirection 0 -TimeoutSec 70
} catch {
    $status = $_.Exception.Response.StatusCode
    if ($status) { throw "WSO2 verification failed with HTTP $([int]$status). Check the agent logs for this invocation." }
    throw "WSO2 verification could not reach the agent or timed out."
} finally {
    Remove-Variable key, credential -ErrorAction SilentlyContinue
}

if ($reply.response -isnot [string]) { throw "WSO2 did not return the expected response string." }
$receipt = $reply.response | ConvertFrom-Json
if ($receipt.service_id -cne "orders") { throw "The receipt did not identify Orders." }
foreach ($resource in @("health", "logs", "metrics")) {
    if ($resource -cnotin @($receipt.resources)) { throw "The agent did not verify a successful $resource read." }
}
if (-not $receipt.selection -or -not @($receipt.selection.observations).Count -or
    $receipt.selection.style -cnotin @("simple", "summary", "technical", "troubleshooting")) {
    throw "The receipt lacks a valid conversational selection."
}
Write-Host "Verified Orders receipt with health, logs, metrics, and style $($receipt.selection.style)."
Write-Host "Confirm the WSO2 serving build SHA and the three monitoring_tool_succeeded entries for this invocation."
