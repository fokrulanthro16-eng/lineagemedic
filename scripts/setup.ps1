<#
.SYNOPSIS
    One-time setup for LineageMedic on Windows.

.DESCRIPTION
    Creates the Python 3.11 virtual environment, installs backend and frontend
    dependencies, and seeds the local SQLite warehouse.

    Python 3.11 specifically: the DataHub CLI publishes no wheels for 3.13+, so
    the project pins py -3.11 to keep the later live-DataHub phase installable
    on the same environment.

    Safe to re-run; every step is idempotent.

.EXAMPLE
    .\scripts\setup.ps1
#>
[CmdletBinding()]
param(
    # Skip npm install when only the backend is needed.
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

Write-Host '=== LineageMedic setup ===' -ForegroundColor Cyan

# -- Python 3.11 -------------------------------------------------------------
$py311 = & py -3.11 --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error @'
Python 3.11 was not found.

  Required action: install Python 3.11 from https://www.python.org/downloads/
  Verify with:     py -3.11 --version
'@
}
Write-Host "Found $py311" -ForegroundColor Green

$venv = Join-Path $repo '.venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    Write-Host 'Creating virtual environment (.venv)...'
    & py -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Error 'Failed to create the virtual environment.' }
} else {
    Write-Host 'Virtual environment already present.' -ForegroundColor Green
}
$python = Join-Path $venv 'Scripts\python.exe'

Write-Host 'Installing Python dependencies...'
& $python -m pip install --quiet --upgrade pip
& $python -m pip install --quiet -e (Join-Path $repo 'packages\lineagemedic')
& $python -m pip install --quiet -e (Join-Path $repo 'apps\api')
& $python -m pip install --quiet pytest pytest-cov ruff mypy httpx 'uvicorn[standard]'
if ($LASTEXITCODE -ne 0) { Write-Error 'Python dependency installation failed.' }
Write-Host 'Python dependencies installed.' -ForegroundColor Green

# -- Local warehouse ---------------------------------------------------------
# The target path is read from the API's own Settings rather than hardcoded, so
# setup always seeds the database the server will actually open.
Write-Host 'Seeding the local SQLite warehouse...'
$env:PYTHONPATH = "$repo\packages\lineagemedic\src;$repo\apps\api"
& $python (Join-Path $repo 'scripts\seed_warehouse.py')
if ($LASTEXITCODE -ne 0) { Write-Error 'Failed to seed the warehouse.' }
Write-Host 'Warehouse seeded.' -ForegroundColor Green

# -- Frontend ----------------------------------------------------------------
if (-not $SkipFrontend) {
    $node = & node --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Error @'
Node.js was not found.

  Required action: install Node.js 20+ from https://nodejs.org/
  Verify with:     node --version
'@
    }
    Write-Host "Found Node $node" -ForegroundColor Green
    Push-Location (Join-Path $repo 'apps\web')
    try {
        Write-Host 'Installing frontend dependencies (this may take a minute)...'
        & npm.cmd install --silent
        if ($LASTEXITCODE -ne 0) { Write-Error 'npm install failed.' }
    } finally {
        Pop-Location
    }
    Write-Host 'Frontend dependencies installed.' -ForegroundColor Green
}

Write-Host ''
Write-Host 'Setup complete.' -ForegroundColor Cyan
Write-Host '  Run the test suites:  .\scripts\test.ps1'
Write-Host '  Start the demo:       .\scripts\start.ps1'
