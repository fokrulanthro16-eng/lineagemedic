<#
.SYNOPSIS
    Run the LineageMedic demo end to end against the live API.

.DESCRIPTION
    Executes all three scenarios, then walks the critical incident through the
    full approval lifecycle and prints what the backend actually returned at
    each step, including the writeback receipt.

    Every number printed comes from an HTTP response. Nothing here is
    hardcoded, so if the backend regresses the demo output changes rather than
    quietly staying pretty.

    Requires the backend to be running (.\scripts\start.ps1 -ApiOnly is enough).

.EXAMPLE
    .\scripts\demo.ps1
#>
[CmdletBinding()]
param(
    [string]$ApiUrl = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'

function Get-Api {
    param([string]$Path)
    try {
        return Invoke-RestMethod -Uri "$ApiUrl$Path" -TimeoutSec 15
    } catch {
        Write-Error @"
Cannot reach the LineageMedic API at $ApiUrl$Path

  Required action: start the backend with .\scripts\start.ps1 -ApiOnly
  Verify with:     Invoke-RestMethod $ApiUrl/health
"@
    }
}

function Invoke-ApiPost {
    param([string]$Path, $Body)
    $request = @{ Uri = "$ApiUrl$Path"; Method = 'Post'; TimeoutSec = 30 }
    if ($null -ne $Body) {
        $request.Body = ($Body | ConvertTo-Json -Compress)
        $request.ContentType = 'application/json'
    }
    return Invoke-RestMethod @request
}

Write-Host '=== LineageMedic demo ===' -ForegroundColor Cyan

# -- Integration status ------------------------------------------------------
$status = Get-Api '/status/integrations'
Write-Host ''
Write-Host 'Integration status' -ForegroundColor Cyan
Write-Host "  mode:             $($status.mode)"
Write-Host "  DataHub connected: $($status.datahub_connected)"
Write-Host "  MCP connected:     $($status.mcp_connected)"
if ($status.fixture_mode_notice) {
    Write-Host "  $($status.fixture_mode_notice)" -ForegroundColor Yellow
}

# -- All scenarios -----------------------------------------------------------
Write-Host ''
Write-Host 'Running every scenario' -ForegroundColor Cyan
$scenarios = Get-Api '/scenarios'
$incidents = @{}

foreach ($scenario in $scenarios) {
    $d = Invoke-ApiPost '/diagnose' @{ scenario_id = $scenario.scenario_id }
    $incidents[$scenario.scenario_id] = $d

    $colour = switch ($d.severity) {
        'critical' { 'Red' }
        'warning'  { 'Yellow' }
        default    { 'Green' }
    }
    Write-Host ''
    Write-Host "  $($scenario.title)" -ForegroundColor White
    Write-Host "    severity:  $($d.severity)" -ForegroundColor $colour
    Write-Host "    affected:  $($d.impact.affected_count)   cleared: $($d.impact.unaffected_count)"
    Write-Host "    checks:    $(@($d.quality_checks).Count) run, $(@($d.quality_checks | Where-Object { $_.status -eq 'fail' }).Count) failed"
    if ($d.root_causes) {
        Write-Host "    root cause: $($d.root_causes[0].summary)"
    }

    # The scenario declares what it expects; the workflow derives severity from
    # measurements. Comparing them here turns the demo into a live assertion.
    if ($d.severity -ne $scenario.expected_severity) {
        Write-Host "    MISMATCH: expected $($scenario.expected_severity)" -ForegroundColor Red
    }
}

# -- Approval lifecycle on the critical incident -----------------------------
$critical = $incidents.Values | Where-Object { $_.severity -eq 'critical' } | Select-Object -First 1
if (-not $critical) {
    Write-Host ''
    Write-Host 'No critical incident was produced; skipping the approval walkthrough.' -ForegroundColor Yellow
    exit 0
}

Write-Host ''
Write-Host "Approval lifecycle for $($critical.incident_id)" -ForegroundColor Cyan
Write-Host "  approval state: $($critical.approval_state)"

Write-Host ''
Write-Host '  Attempting a writeback BEFORE approval (expected to be refused)...'
try {
    Invoke-ApiPost "/incidents/$($critical.incident_id)/writeback" $null | Out-Null
    Write-Host '    UNEXPECTED: the writeback was not refused.' -ForegroundColor Red
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    Write-Host "    refused with HTTP $code - the approval gate holds." -ForegroundColor Green
}

Write-Host ''
Write-Host '  Approving the remediation plan...'
$approval = Invoke-ApiPost "/incidents/$($critical.incident_id)/approve" @{
    approved = $true; approver = 'demo-operator'; note = 'Approved during demo run.'
}
Write-Host "    approval state: $($approval.approval_state)" -ForegroundColor Green

Write-Host ''
Write-Host '  Attempting the writeback after approval...'
$receipt = Invoke-ApiPost "/incidents/$($critical.incident_id)/writeback" $null
$receiptColour = if ($receipt.status -eq 'applied') { 'Green' } else { 'Yellow' }
Write-Host "    status: $($receipt.status)" -ForegroundColor $receiptColour
Write-Host "    $($receipt.note)"

Write-Host ''
if ($receipt.status -eq 'skipped_fixture_mode') {
    Write-Host 'Fixture mode: no DataHub is connected, so nothing was written.' -ForegroundColor Yellow
    Write-Host 'The receipt above says so explicitly rather than reporting success.'
}
Write-Host 'Demo complete.' -ForegroundColor Cyan

