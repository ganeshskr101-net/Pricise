# One-time setup (Windows PowerShell): virtual env, Python packages, Playwright's Chromium, and .env from .env.example.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#         (use another Python with:  $env:PYTHON = "C:\Path\To\python.exe"; then run it again)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Invoke-Step([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Failed: $exe $($arguments -join ' ')" }
}

if ($env:PYTHON) { $py = $env:PYTHON; $pyArgs = @() }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $py = "py"; $pyArgs = @("-3") }
else { $py = "python"; $pyArgs = @() }

& $py @pyArgs -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Pricise needs Python 3.11 or newer. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'),"
    Write-Host "or set `$env:PYTHON to its python.exe and run this again."
    exit 1
}

if (-not (Test-Path ".venv")) { Invoke-Step $py ($pyArgs + @("-m", "venv", ".venv")) }
$venvPy = ".venv\Scripts\python.exe"
Invoke-Step $venvPy @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Step $venvPy @("-m", "pip", "install", "--quiet", "-r", "backend\requirements-dev.txt")
# The hidden browser for Store Lab checks. Only the real Deal Agent needs it, so a failed download doesn't stop setup.
& $venvPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: couldn't download Playwright's Chromium. Stub mode works without it; for the real Deal Agent run:"
    Write-Host "         .venv\Scripts\python.exe -m playwright install chromium"
}
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example (stub mode, no keys needed)."
}
Write-Host ""
Write-Host "Setup done. Start the backend:  scripts\run.ps1        (real Deal Agent: scripts\run.ps1 -Real)"
Write-Host "Run the tests:                  scripts\test.ps1"
