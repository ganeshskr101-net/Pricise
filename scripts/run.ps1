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

$uvicornArgs = @("-m", "uvicorn", "backend.stub_server:app", "--port", "8787")
if ($Reload) { $uvicornArgs += "--reload" }
& $venvPy @uvicornArgs
exit $LASTEXITCODE
