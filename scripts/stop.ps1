# Stop the SuperAgent daemon and its child agent processes.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "var\daemon.pid"
if (-not (Test-Path $pidFile)) { Write-Output "not running (no pid file)"; exit 0 }
$daemonPid = Get-Content $pidFile
if ($daemonPid -and (Get-Process -Id $daemonPid -ErrorAction SilentlyContinue)) {
  & taskkill /F /T /PID $daemonPid | Out-Null
  Write-Output "stopped pid $daemonPid"
} else {
  Write-Output "process $daemonPid not running"
}
Remove-Item $pidFile -ErrorAction SilentlyContinue
