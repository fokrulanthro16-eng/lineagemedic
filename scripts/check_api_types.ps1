<#
.SYNOPSIS
    Fail if the committed API schema or frontend types are stale.

.DESCRIPTION
    The frontend's types are generated from the backend's OpenAPI schema, which
    is itself generated from the Pydantic models. That chain only protects
    against drift if someone notices when a link is out of date -- so this
    regenerates both artifacts and fails if the result differs from what is
    committed.

    Without this, changing a response model and forgetting to regenerate would
    leave the dashboard compiling happily against types the API no longer
    serves.

    Run it after any change to the API models, and let CI run it on every push.

.EXAMPLE
    .\scripts\check_api_types.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Error 'Virtual environment missing. Run .\scripts\setup.ps1 first.'
}

$env:PYTHONPATH = "$repo\packages\lineagemedic\src;$repo\apps\api"

Write-Host 'Regenerating the OpenAPI schema and frontend types...' -ForegroundColor Cyan
& $python (Join-Path $repo 'scripts\export_openapi.py')
if ($LASTEXITCODE -ne 0) { Write-Error 'Could not export the OpenAPI schema.' }

Push-Location (Join-Path $repo 'apps\web')
try {
    & npm.cmd run generate:types --silent
    if ($LASTEXITCODE -ne 0) { Write-Error 'Could not generate the TypeScript types.' }
} finally {
    Pop-Location
}

# --quiet exits non-zero when the working tree differs from the index/HEAD for
# these paths, which is precisely "the committed artifact is stale".
& git -C $repo diff --quiet -- scripts/openapi.json apps/web/src/api/schema.ts
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'The generated API contract is out of date:' -ForegroundColor Red
    & git -C $repo --no-pager diff --stat -- scripts/openapi.json apps/web/src/api/schema.ts
    Write-Host ''
    Write-Host 'Commit the regenerated files above to bring the frontend back in step.' -ForegroundColor Yellow
    exit 1
}

Write-Host 'API schema and frontend types are in sync.' -ForegroundColor Green
