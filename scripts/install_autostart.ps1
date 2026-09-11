<#
Registers (or re-registers) the logon task that runs scripts/autostart_launcher.py.

Per-user, so it needs no elevation. The task itself decides nothing: the launcher
reads the wfm.autostart marker, which `wfm daemon start` writes and `wfm daemon stop`
deletes. Removing the task: Unregister-ScheduledTask -TaskName WFMStalkerAutostart.
#>
param(
    [string]$TaskName = "WFMStalkerAutostart"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $python)) { $python = Join-Path $repo ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { throw "no interpreter at $python -- create the venv first" }
$launcher = Join-Path $repo "scripts\autostart_launcher.py"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$launcher`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# A laptop is usually on battery at logon; without these the task silently never runs.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output "registered $TaskName -> $python $launcher"
