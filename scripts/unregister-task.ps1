<#
.SYNOPSIS
  Removes the scheduled sync. Does not touch credentials or the sheet.
  powershell -ExecutionPolicy Bypass -File .\scripts\unregister-task.ps1
#>
Unregister-ScheduledTask -TaskPath '\FinanceSync\' -TaskName 'Finance Sync' -Confirm:$false
Write-Host "Scheduled task removed."
