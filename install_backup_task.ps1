<#
.SYNOPSIS
  Register / remove the daily Windows scheduled task that pulls the
  trade full-database backup artifact back to local disk and verifies it.
  ASCII-only: Windows PowerShell 5.1 reads .ps1 as ANSI unless a BOM
  is present, so non-ASCII text would corrupt the parser.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install_backup_task.ps1
  powershell -ExecutionPolicy Bypass -File .\install_backup_task.ps1 -At "12:30"
  powershell -ExecutionPolicy Bypass -File .\install_backup_task.ps1 -Remove
#>
param(
    [string]$At = "12:30",
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'WorkBuddy-Trade-DB-Backup-Pull'

if ($Remove) {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "REMOVED $taskName"
    } else {
        Write-Output "NOT_FOUND $taskName"
    }
    return
}

$launcher = 'C:\Windows\py.exe'
if (-not (Test-Path $launcher)) {
    $cmd = Get-Command py -ErrorAction Stop
    $launcher = $cmd.Source
}
$script = Join-Path $PSScriptRoot 'fetch_db_backup.py'
if (-not (Test-Path $script)) { throw "Script not found: $script" }

$action = New-ScheduledTaskAction -Execute $launcher `
    -Argument ('-3.11 "' + $script + '"') -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description 'Pull the trade full-database backup artifact to local disk and verify it daily' | Out-Null

$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Output ("INSTALLED {0} | state={1} | next={2} | script={3}" -f `
    $taskName, $task.State, $info.NextRunTime, $script)
