# Export a portable JSON snapshot (db contents + artifacts manifest).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $root "var\backups"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Push-Location $root
try {
  python -m superagent export (Join-Path $outDir "super-$stamp.json")
} finally {
  Pop-Location
}
Write-Output "backup written to $outDir"
