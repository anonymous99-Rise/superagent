$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "SuperAgent" -Confirm:$false
Write-Output "removed scheduled task 'SuperAgent'"
