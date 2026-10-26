<#
.SYNOPSIS
  One-time install for Finance Sync: creates .venv, installs the package,
  creates config\config.toml, then runs `doctor`.

.HOW TO RUN
  Open PowerShell in the "Finance Sync" folder and run:
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
  Safe to re-run (it upgrades in place).
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11') { $candidates += , @('py', "-$v") }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @('python') }
    foreach ($c in $candidates) {
        $exe = $c[0]
        $pyArgs = @($c | Select-Object -Skip 1)
        try {
            $out = & $exe @pyArgs -c "import sys; print(sys.executable); print(sys.version_info >= (3, 11))" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and $out[1] -eq 'True') { return $out[0] }
        } catch { }
    }
    return $null
}

Write-Host "== Finance Sync install ==" -ForegroundColor Cyan
Write-Host "Project folder: $Root"

$py = Find-Python
if (-not $py) {
    Write-Host ""
    Write-Host "Python 3.11 or newer was not found." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/windows/"
    Write-Host "  - tick 'Add python.exe to PATH' on the first installer screen"
    Write-Host "Then close and reopen PowerShell and run this script again."
    exit 1
}
Write-Host "Using Python: $py"

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host "Creating virtual environment (.venv)..."
    & $py -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Creating .venv failed" }
}
$vpy = Join-Path $Root '.venv\Scripts\python.exe'

Write-Host "Installing dependencies (one minute)..."
& $vpy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $vpy -m pip install -e . --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

New-Item -ItemType Directory -Force -Path (Join-Path $Root 'data'), (Join-Path $Root 'logs') | Out-Null
if (-not (Test-Path 'config\config.toml')) {
    Copy-Item 'config\config.example.toml' 'config\config.toml'
    Write-Host "Created config\config.toml"
}

Write-Host ""
Write-Host "== Health check (FAIL items are expected until setup is done) ==" -ForegroundColor Cyan
& $vpy -m finance_sync doctor

Write-Host ""
Write-Host "Installed. Next, from this folder run, in order:" -ForegroundColor Green
Write-Host "  .\scripts\finance-sync.ps1 setup-google `"C:\path\to\downloaded-key.json`""
Write-Host "  .\scripts\finance-sync.ps1 set-sheet `"<your Google Sheet URL>`""
Write-Host "  .\scripts\finance-sync.ps1 setup-simplefin"
Write-Host "  .\scripts\finance-sync.ps1 init-sheet"
Write-Host "  .\scripts\finance-sync.ps1 doctor --online"
Write-Host "  .\scripts\finance-sync.ps1 sync --dry-run"
Write-Host "  .\scripts\finance-sync.ps1 sync --force"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\register-task.ps1"
