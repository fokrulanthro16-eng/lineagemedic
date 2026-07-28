<#
.SYNOPSIS
    Stop the LineageMedic backend and dashboard.

.DESCRIPTION
    Terminates only processes belonging to this repository. Primary source of
    truth is logs\pids.json, written by start.ps1; each recorded PID is
    re-validated against the live process table before anything is killed, so a
    stale file whose PID has been recycled cannot take down an unrelated
    process.

    Falls back to matching command lines that contain this repository's path,
    which also catches servers started by hand. Unrelated Python or Node
    processes on the machine are never touched.

.EXAMPLE
    .\scripts\stop.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
# Regex-escape the path: it contains backslashes and may contain other
# metacharacters, which would otherwise widen the match.
$escaped = [regex]::Escape($repo)
$pidFile = Join-Path $repo 'logs\pids.json'

$live = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'"

# -- Recorded PIDs, re-validated --------------------------------------------
$recorded = @()
if (Test-Path $pidFile) {
    try {
        $entries = Get-Content $pidFile -Raw | ConvertFrom-Json
        foreach ($entry in @($entries)) {
            $match = $live | Where-Object { $_.ProcessId -eq $entry.pid }
            # A PID alone proves nothing: Windows recycles them. Require the
            # process to have started no earlier than the moment we recorded it.
            if ($match -and $match.CreationDate -ge [datetime]$entry.started) {
                $recorded += $match
            }
        }
    } catch {
        Write-Verbose "Ignoring unreadable PID file: $_"
    }
}

# -- Command-line match (covers manually started servers) --------------------
$byPath = $live | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -match $escaped -and
    $_.CommandLine -match 'lineagemedic_api\.main|vite'
}

$targets = @($recorded) + @($byPath) | Sort-Object -Property ProcessId -Unique

if (-not $targets) {
    Write-Host 'No LineageMedic processes are running.' -ForegroundColor Yellow
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force }
    exit 0
}

foreach ($proc in $targets) {
    Write-Host "Stopping PID $($proc.ProcessId) ($($proc.Name))" -ForegroundColor Cyan
    # Kill the tree: `npm run dev` spawns vite as a child, and stopping only the
    # wrapper would leave the dev server holding its port.
    & taskkill.exe /PID $proc.ProcessId /T /F 2>&1 | Out-Null
}

if (Test-Path $pidFile) { Remove-Item $pidFile -Force }

Start-Sleep -Seconds 1
Write-Host 'Stopped.' -ForegroundColor Green
