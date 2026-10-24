# Runs the backend tests. Extra arguments go to pytest, e.g.  scripts\test.ps1 -k chat -x
# Tests that need the hidden browser skip themselves if Chromium isn't installed.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\test.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$venvPy = ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Host "Run scripts\setup.ps1 first."; exit 1 }
& $venvPy -m pytest backend/tests @args
exit $LASTEXITCODE
