<#
.SYNOPSIS
  Registers the daily sync in Windows Task Scheduler as \FinanceSync\Finance Sync.

  - Runs daily at -Time (default 07:15) AND 10 minutes after you log on.
  - "Run as soon as possible after a scheduled start is missed" is ON, so a
    laptop that was asleep at 07:15 syncs when it wakes.
  - The program itself skips runs within 20h of the last success, so the
    two triggers never burn extra SimpleFIN requests.
  - Runs as YOU, only while you're logged on (needed to read the vault key
    from Credential Manager). No stored password, no admin rights.
  - Uses pythonw.exe: no console window pops up.

.HOW TO RUN
  powershell -ExecutionPolicy Bypass -File .\scripts\register-task.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\register-task.ps1 -Time 06:40
#>
param([string]$Time = '07:15')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$pyw = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pyw)) { throw "Run scripts\install.ps1 first ($pyw not found)." }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "`"$pyw`"" -Argument '-m finance_sync sync' -WorkingDirectory $Root
$daily = New-ScheduledTaskTrigger -Daily -At $Time
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Delay = 'PT10M'
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName 'Finance Sync' `
    -TaskPath '\FinanceSync\' `
    -Action $action `
    -Trigger @($daily, $logon) `
    -Settings $settings `
    -Principal $principal `
    -Description 'Read-only SimpleFIN -> Google Sheets transaction sync. See Finance Sync\README.md' `
    -Force | Out-Null

Write-Host "Registered \FinanceSync\Finance Sync for $user (daily $Time + at logon)." -ForegroundColor Green
Write-Host "Test it now:   Start-ScheduledTask -TaskPath '\FinanceSync\' -TaskName 'Finance Sync'"
Write-Host "Check result:  Get-ScheduledTaskInfo -TaskPath '\FinanceSync\' -TaskName 'Finance Sync'  (LastTaskResult 0 = OK)"
Write-Host "Details are in logs\finance-sync.log and the 'Sync Log' tab of your sheet."
