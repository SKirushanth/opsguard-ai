param([int]$FrontendPort = 5173)

$ErrorActionPreference = 'Stop'
$frontend = Join-Path $PSScriptRoot 'frontend'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$vite = Join-Path $frontend 'node_modules\vite\bin\vite.js'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $vite)) {
    throw 'Install the Python and frontend dependencies using the local setup steps in README.md.'
}
$node = (Get-Command node.exe).Source
$servers = @{}
try {
    foreach ($service in @('backend', 'agent')) {
        $module = if ($service -eq 'backend') { 'backend.app.main' } else { 'agent.main' }
        $servers[$service] = Start-Process $python -ArgumentList '-m', $module -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
    }
    $servers['frontend'] = Start-Process $node -ArgumentList 'node_modules/vite/bin/vite.js', '--host', 'localhost', '--strictPort', '--port', $FrontendPort -WorkingDirectory $frontend -NoNewWindow -PassThru
    Write-Host "Starting OpsGuard at http://localhost:$FrontendPort. Press Ctrl+C to stop all three servers."
    while ($true) {
        foreach ($service in $servers.Keys) {
            if ($servers[$service].HasExited) {
                throw "The $service server exited. Check the output above for details."
            }
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    foreach ($process in $servers.Values) {
        if (!$process.HasExited) {
            # The Windows virtual environment launcher can have a Python child.
            taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
        }
    }
}
