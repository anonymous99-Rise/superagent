# Register a per-user scheduled task so SuperAgent starts at logon.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$start = Join-Path $PSScriptRoot "start.ps1"
$taskName = "SuperAgent"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$start`"" `
  -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
  -Description "SuperAgent proactive mission control" -Force | Out-Null

$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "SuperAgent 看板.url"
Set-Content -Path $shortcut -Encoding ASCII -Value @(
  "[InternetShortcut]",
  "URL=http://127.0.0.1:8787",
  "IconIndex=0"
)

Write-Output "registered scheduled task '$taskName' (starts at logon)"
Write-Output "desktop shortcut: $shortcut"
Write-Output "start now with: powershell -File `"$start`""
