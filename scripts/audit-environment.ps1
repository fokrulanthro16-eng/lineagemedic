#Requires -Version 5.1
<#
.SYNOPSIS
    LineageMedic Phase 0 environment audit.
.DESCRIPTION
    Re-runs every check recorded in docs/ENVIRONMENT_AUDIT.md and prints a pass/fail table.
    Reads no credentials and emits no secrets. Read-only: installs and changes nothing.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\audit-environment.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$results = New-Object System.Collections.Generic.List[object]

function Add-Result {
    param(
        [Parameter(Mandatory)][string]$Item,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Detected,
        [Parameter(Mandatory)][string]$Required,
        [Parameter(Mandatory)][ValidateSet('PASS', 'WARN', 'FAIL')][string]$Status
    )
    $results.Add([pscustomobject]@{
        Item     = $Item
        Detected = $(if ([string]::IsNullOrWhiteSpace($Detected)) { 'not detected' } else { $Detected })
        Required = $Required
        Status   = $Status
    })
}

function Get-ToolVersion {
    <# Returns the first line of a tool's version output, or $null if the tool is absent. #>
    param(
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$VersionArgs
    )
    $exe = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $exe) { return $null }
    try {
        $out = & $exe.Source @VersionArgs 2>$null | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($out)) { return $null }
        return $out.Trim()
    } catch {
        return $null
    }
}

Write-Host ''
Write-Host 'LineageMedic - Phase 0 Environment Audit' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''

# --- Operating system, memory, disk -----------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
Add-Result 'Windows version' "$($os.Caption) $($os.Version)" 'Windows 10/11 x64' 'PASS'

$totalRamGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2)
Add-Result 'Total RAM' "$totalRamGb GB" '8 GB min / 16 GB recommended' `
    $(if ($totalRamGb -ge 15) { 'PASS' } elseif ($totalRamGb -ge 8) { 'WARN' } else { 'FAIL' })

$freeRamGb = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
Add-Result 'Free RAM' "$freeRamGb GB" '>= 6 GB free before DataHub Quickstart' `
    $(if ($freeRamGb -ge 6) { 'PASS' } else { 'WARN' })

$freeDiskGb = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
Add-Result 'Free disk (C:)' "$freeDiskGb GB" '>= 20 GB' `
    $(if ($freeDiskGb -ge 20) { 'PASS' } else { 'FAIL' })

# --- Core toolchain ----------------------------------------------------------
$git = Get-ToolVersion 'git' @('--version')
Add-Result 'Git' $git 'any 2.x' $(if ($git) { 'PASS' } else { 'FAIL' })

$gh = Get-ToolVersion 'gh' @('--version')
Add-Result 'GitHub CLI (gh)' $gh 'optional' $(if ($gh) { 'PASS' } else { 'WARN' })

$python = Get-ToolVersion 'python' @('--version')
Add-Result 'Python (default)' $python '3.10+' $(if ($python) { 'PASS' } else { 'FAIL' })

# DataHub CLI needs 3.10-3.11; the project pins py -3.11 explicitly.
$py311 = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    try { $py311 = (& py -3.11 --version 2>$null | Select-Object -First 1) } catch { $py311 = $null }
}
Add-Result 'Python 3.11 (py -3.11)' $py311 '3.10-3.11 for DataHub CLI' `
    $(if ($py311) { 'PASS' } else { 'FAIL' })

$node = Get-ToolVersion 'node' @('--version')
Add-Result 'Node.js' $node '18+' $(if ($node) { 'PASS' } else { 'FAIL' })

$npm = Get-ToolVersion 'npm' @('--version')
Add-Result 'npm' $npm '9+' $(if ($npm) { 'PASS' } else { 'FAIL' })

# --- Container runtime (the hard requirement for DataHub Quickstart) ---------
$docker = Get-ToolVersion 'docker' @('--version')
Add-Result 'Docker' $docker 'required for DataHub Quickstart' `
    $(if ($docker) { 'PASS' } else { 'FAIL' })

$compose = $null
if ($docker) { $compose = Get-ToolVersion 'docker' @('compose', 'version') }
Add-Result 'Docker Compose v2' $compose 'required' $(if ($compose) { 'PASS' } else { 'FAIL' })

$uv = Get-ToolVersion 'uv' @('--version')
Add-Result 'uv' $uv 'optional' $(if ($uv) { 'PASS' } else { 'WARN' })

# --- Virtualization prerequisites -------------------------------------------
$hypervisor = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent
Add-Result 'Hardware virtualization' "HypervisorPresent=$hypervisor" 'required' `
    $(if ($hypervisor) { 'PASS' } else { 'FAIL' })

$wslDistros = $null
if (Get-Command wsl -ErrorAction SilentlyContinue) {
    # wsl.exe emits UTF-16LE; strip embedded nulls so the text is greppable.
    $raw = (wsl -l -q 2>&1 | Out-String) -replace "`0", ''
    $wslDistros = ($raw -split "`r?`n" | Where-Object { $_.Trim() }) -join ', '
}
Add-Result 'WSL distributions' $wslDistros 'Docker Desktop provides its own' `
    $(if ($wslDistros) { 'PASS' } else { 'WARN' })

# --- Ports -------------------------------------------------------------------
$portPurpose = @{
    8000 = 'LineageMedic API'
    5173 = 'Vite dev server'
    8080 = 'DataHub GMS'
    9002 = 'DataHub Frontend UI'
}
foreach ($port in 8000, 5173, 8080, 9002) {
    $inUse = Test-NetConnection -ComputerName 127.0.0.1 -Port $port `
        -WarningAction SilentlyContinue -InformationLevel Quiet
    Add-Result "Port $port ($($portPurpose[$port]))" $(if ($inUse) { 'IN USE' } else { 'FREE' }) 'free' `
        $(if ($inUse) { 'WARN' } else { 'PASS' })
}

# --- Report ------------------------------------------------------------------
$results | Format-Table -AutoSize -Property Item, Detected, Required, Status

$failures = @($results | Where-Object Status -eq 'FAIL')
$warnings = @($results | Where-Object Status -eq 'WARN')

Write-Host ''
Write-Host "PASS: $(@($results | Where-Object Status -eq 'PASS').Count)  WARN: $($warnings.Count)  FAIL: $($failures.Count)"
Write-Host ''

if ($failures.Count -gt 0) {
    Write-Host 'BLOCKED. The following requirements are not met:' -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $($f.Item): $($f.Detected)" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'See docs\ENVIRONMENT_AUDIT.md section 3 for remediation commands.' -ForegroundColor Yellow
    exit 1
}

Write-Host 'All required baselines satisfied.' -ForegroundColor Green
exit 0
