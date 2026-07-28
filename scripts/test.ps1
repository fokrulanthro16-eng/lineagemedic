<#
.SYNOPSIS
    Run every quality gate: lint, type checks, and both test suites.

.DESCRIPTION
    Runs the same commands CI runs, in the same order, and exits non-zero if any
    of them fail. Each gate runs even if an earlier one failed, so a single
    invocation reports every problem rather than only the first.

.EXAMPLE
    .\scripts\test.ps1
    .\scripts\test.ps1 -BackendOnly
#>
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [switch]$Coverage
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Error 'Virtual environment missing. Run .\scripts\setup.ps1 first.'
}

$env:PYTHONPATH = "$repo\packages\lineagemedic\src;$repo\apps\api"
$results = [ordered]@{}

function Invoke-Gate {
    param([string]$Name, [scriptblock]$Action)

    Write-Host ''
    Write-Host "--- $Name ---" -ForegroundColor Cyan
    & $Action
    $ok = $LASTEXITCODE -eq 0
    $script:results[$Name] = $ok
    if ($ok) {
        Write-Host "$Name passed" -ForegroundColor Green
    } else {
        Write-Host "$Name FAILED" -ForegroundColor Red
    }
}

if (-not $FrontendOnly) {
    Push-Location $repo
    try {
        Invoke-Gate 'ruff' { & $python -m ruff check . }
        Invoke-Gate 'mypy' {
            & $python -m mypy packages/lineagemedic/src/lineagemedic apps/api/lineagemedic_api tests
        }
        Invoke-Gate 'pytest' {
            if ($Coverage) {
                & $python -m pytest --cov=lineagemedic --cov=lineagemedic_api --cov-report=term-missing
            } else {
                & $python -m pytest
            }
        }
    } finally {
        Pop-Location
    }
}

if (-not $BackendOnly) {
    Push-Location (Join-Path $repo 'apps\web')
    try {
        Invoke-Gate 'tsc' { & npm.cmd run typecheck --silent }
        Invoke-Gate 'vitest' {
            if ($Coverage) { & npm.cmd run test:coverage --silent } else { & npm.cmd test --silent }
        }
    } finally {
        Pop-Location
    }
}

Write-Host ''
Write-Host '=== Summary ===' -ForegroundColor Cyan
foreach ($gate in $results.Keys) {
    $status = if ($results[$gate]) { 'PASS' } else { 'FAIL' }
    $colour = if ($results[$gate]) { 'Green' } else { 'Red' }
    Write-Host ("  {0,-8} {1}" -f $gate, $status) -ForegroundColor $colour
}

$failed = @($results.Values | Where-Object { -not $_ }).Count
if ($failed -gt 0) {
    Write-Host ''
    Write-Host "$failed gate(s) failed." -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host 'All gates passed.' -ForegroundColor Green
