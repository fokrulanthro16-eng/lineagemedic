<#
.SYNOPSIS
    Start the LineageMedic backend and dashboard.

.DESCRIPTION
    Launches uvicorn and the Vite dev server as background processes, waits for
    both to answer, and prints the URLs. Logs stream to logs\ so a failed start
    is diagnosable rather than silent.

    Stop them again with .\scripts\stop.ps1.

.EXAMPLE
    .\scripts\start.ps1
    .\scripts\start.ps1 -ApiOnly
#>
[CmdletBinding()]
param(
    [switch]$ApiOnly,
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Error 'Virtual environment missing. Run .\scripts\setup.ps1 first.'
}

$logs = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

<#
    Wait until a URL answers. Returns $true on success. Polls rather than
    sleeping a fixed interval so a fast machine is not penalised and a slow one
    is not cut off early.
#>
function Wait-ForUrl {
    param([string]$Url, [int]$TimeoutSeconds = 45)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

Write-Host '=== Starting LineageMedic ===' -ForegroundColor Cyan

$env:PYTHONPATH = "$repo\packages\lineagemedic\src;$repo\apps\api"
$api = Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'lineagemedic_api.main:app', '--host', '127.0.0.1', '--port', $ApiPort `
    -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs 'api.out.log') `
    -RedirectStandardError (Join-Path $logs 'api.err.log')

if (-not (Wait-ForUrl "http://127.0.0.1:$ApiPort/health")) {
    Write-Host 'Backend failed to start. Last log lines:' -ForegroundColor Red
    Get-Content (Join-Path $logs 'api.err.log') -Tail 20 -ErrorAction SilentlyContinue
    Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
    Write-Error 'Backend did not become healthy.'
}
Write-Host "Backend ready:  http://127.0.0.1:$ApiPort  (docs at /docs)" -ForegroundColor Green

if (-not $ApiOnly) {
    $web = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run', 'dev' `
        -WorkingDirectory (Join-Path $repo 'apps\web') -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $logs 'web.out.log') `
        -RedirectStandardError (Join-Path $logs 'web.err.log')

    # Vite binds localhost (IPv6) by default, so probe that name rather than
    # 127.0.0.1, which can refuse the connection even when the server is up.
    if (-not (Wait-ForUrl "http://localhost:$WebPort/")) {
        Write-Host 'Dashboard failed to start. Last log lines:' -ForegroundColor Red
        Get-Content (Join-Path $logs 'web.out.log') -Tail 20 -ErrorAction SilentlyContinue
        Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue
        Stop-Process -Id $api.Id -Force -ErrorAction SilentlyContinue
        Write-Error 'Dashboard did not start.'
    }
    Write-Host "Dashboard ready: http://localhost:$WebPort" -ForegroundColor Green
}

# Record what we started so stop.ps1 can target exactly these processes. The
# start time is stored alongside each PID because Windows recycles PIDs: on
# shutdown the recorded time is compared against the live process so a stale
# entry cannot match something unrelated.
$record = @(
    [pscustomobject]@{ name = 'api'; pid = $api.Id; started = $api.StartTime.ToString('o') }
)
if (-not $ApiOnly) {
    $record += [pscustomobject]@{ name = 'web'; pid = $web.Id; started = $web.StartTime.ToString('o') }
}
$record | ConvertTo-Json -Depth 3 | Set-Content -Path (Join-Path $logs 'pids.json') -Encoding utf8

Write-Host ''
Write-Host 'Services are running in the background.' -ForegroundColor Cyan
Write-Host '  Logs:  logs\api.err.log, logs\web.out.log'
Write-Host '  Stop:  .\scripts\stop.ps1'
