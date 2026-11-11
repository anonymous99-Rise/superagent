# Start SuperAgent daemon in the background (dashboard + patrol + dispatcher).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
  # Scheduled tasks start with a minimal PATH; fall back to the per-user install.
  $py = Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") `
    -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
    Select-Object -First 1 -ExpandProperty FullName
}
if (-not $py) { throw "python not found in PATH" }

$logDir = Join-Path $root "var\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$pidFile = Join-Path $root "var\daemon.pid"
if (Test-Path $pidFile) {
  $old = Get-Content $pidFile -ErrorAction SilentlyContinue
  if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
    Write-Output "SuperAgent already running (pid $old)"
    exit 0
  }
}

$out = Join-Path $logDir "daemon.log"
$err = Join-Path $logDir "daemon.err.log"
$p = Start-Process -FilePath $py -ArgumentList "-m", "superagent", "daemon" `
  -WorkingDirectory $root -WindowStyle Hidden `
  -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
$p.Id | Set-Content -Path $pidFile -Encoding ascii
Write-Output "SuperAgent started (pid $($p.Id))"
Write-Output "Dashboard: http://127.0.0.1:8787"
Write-Output "Logs: $out"
