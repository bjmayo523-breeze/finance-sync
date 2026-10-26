<#
.SYNOPSIS
  Run any Finance Sync command with the project's own Python.
  Examples:
    .\scripts\finance-sync.ps1 doctor
    .\scripts\finance-sync.ps1 sync --dry-run
    .\scripts\finance-sync.ps1 recategorize
  If PowerShell blocks the script, run once per window:
    Set-ExecutionPolicy -Scope Process Bypass
#>
$Root = Split-Path -Parent $PSScriptRoot
$vpy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) {
    Write-Host "Not installed yet. Run: powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1" -ForegroundColor Red
    exit 1
}
Push-Location $Root
try {
    & $vpy -m finance_sync @args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
