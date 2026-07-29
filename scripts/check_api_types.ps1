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

<#
    Run git and return only its exit code, discarding whatever it wrote to
    stderr.

    This exists to work around Windows PowerShell, not to weaken the check.
    `.gitattributes` normalises the generated files to LF, while a Windows
    checkout with core.autocrlf=true writes them back as CRLF, so git emits

        warning: in the working copy of 'scripts/openapi.json',
        CRLF will be replaced by LF the next time Git touches it

    on **stderr** whenever it inspects a file that was just regenerated. Windows
    PowerShell 5.1 turns any native-command stderr output into a
    NativeCommandError, which failed this script even when the contract was
    perfectly in sync. An in-process `2>$null` is not enough - 5.1 still wraps
    each stderr line in an ErrorRecord - so stderr is redirected at the process
    level instead.

    Only the advisory text is discarded. The value returned is git's own exit
    code, so a genuinely stale artifact still fails exactly as before.
#>
function Invoke-GitExitCode {
    param([string[]]$GitArgs)

    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath 'git' -ArgumentList $GitArgs `
            -NoNewWindow -Wait -PassThru -RedirectStandardError $errFile
        return $p.ExitCode
    } finally {
        Remove-Item $errFile -Force -ErrorAction SilentlyContinue
    }
}

# --quiet exits non-zero when the working tree differs from the index/HEAD for
# these paths, which is precisely "the committed artifact is stale".
$diffExit = Invoke-GitExitCode @(
    '-C', $repo, 'diff', '--quiet', '--',
    'scripts/openapi.json', 'apps/web/src/api/schema.ts'
)
if ($diffExit -ne 0) {
    Write-Host ''
    Write-Host 'The generated API contract is out of date:' -ForegroundColor Red
    # The diff itself must reach the operator, so this captures stdout rather
    # than discarding it. cmd.exe drops stderr before PowerShell can turn the
    # CRLF advisory into a NativeCommandError that would mask the very diff
    # this failure path exists to show.
    $stat = & cmd.exe /c "git -C ""$repo"" --no-pager diff --stat -- scripts/openapi.json apps/web/src/api/schema.ts 2>NUL"
    $stat | ForEach-Object { Write-Host $_ }
    Write-Host ''
    Write-Host 'Commit the regenerated files above to bring the frontend back in step.' -ForegroundColor Yellow
    exit 1
}

Write-Host 'API schema and frontend types are in sync.' -ForegroundColor Green
