# Starts the Pricise backend on http://localhost:8787 (the port the extension calls).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [-Real | -Stub] [-Reload]
#   -Real    AGENT_DEAL=real: the real Deal Agent (hidden browser on Store Lab pages)
#   -Stub    AGENT_DEAL=stub: the scripted demo (the default when .env doesn't say otherwise)
#   -Reload  restart automatically when backend code changes
param([switch]$Real, [switch]$Stub, [switch]$Reload)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$venvPy = ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Run scripts\setup.ps1 first."; exit 1 }
if ($Real) { $env:AGENT_DEAL = "real" }
if ($Stub) { $env:AGENT_DEAL = "stub" }
$mode = if ($env:AGENT_DEAL) { $env:AGENT_DEAL } else { "from .env, default stub" }
Write-Host "Pricise backend on http://localhost:8787  (AGENT_DEAL=$mode)"

# The engine sidecar on 127.0.0.1:8788 (same as: cd backend\engines\world_model_ts; npx tsx server.ts), started here
# and stopped when the backend stops. Optional: without Node 20+ the backend works on its own.
$sidecar = $null
$tsx = "backend\engines\world_model_ts\node_modules\tsx\dist\cli.mjs"
$up = $false
try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "http://127.0.0.1:8788/health" | Out-Null; $up = $true } catch { }
$nodeMajor = 0
if (Get-Command node -ErrorAction SilentlyContinue) { $nodeMajor = [int](& node -p "process.versions.node.split('.')[0]") }
if ($up) { Write-Host "Engine sidecar already running on http://127.0.0.1:8788" }
elseif ($nodeMajor -ge 20 -and (Test-Path $tsx)) {
    $sidecar = Start-Process -FilePath "node" -ArgumentList "node_modules/tsx/dist/cli.mjs", "server.ts" `
        -WorkingDirectory "backend\engines\world_model_ts" -NoNewWindow -PassThru
    Write-Host "Engine sidecar on http://127.0.0.1:8788 (stops with the backend)"
}
else { Write-Host "Engine sidecar not started (needs Node 20+ and scripts\setup.ps1); the backend works without it." }

$uvicornArgs = @("-m", "uvicorn", "backend.stub_server:app", "--port", "8787")
if ($Reload) { $uvicornArgs += "--reload" }
$code = 1
try {
    & $venvPy @uvicornArgs
    $code = $LASTEXITCODE
} finally {
    if ($sidecar -and -not $sidecar.HasExited) { Stop-Process -Id $sidecar.Id -Force }
}
exit $code
