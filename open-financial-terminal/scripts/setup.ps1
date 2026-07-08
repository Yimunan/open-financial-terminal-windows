# One-time setup for Open Financial Terminal (Windows / PowerShell).
# Creates the backend venv, installs qhfi (editable) + backend deps, and npm installs the frontend.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$qhfi = Join-Path (Split-Path -Parent $root) "quant-hedge-fund-incubator"

if (-not (Test-Path $qhfi)) {
    throw "qhfi engine not found at $qhfi. Clone quant-hedge-fund-incubator as a sibling directory."
}

Write-Host "==> Creating backend venv" -ForegroundColor Cyan
python -m venv (Join-Path $backend ".venv")
$py = Join-Path $backend ".venv\Scripts\python.exe"

Write-Host "==> Installing qhfi (editable) + its dependencies" -ForegroundColor Cyan
& $py -m pip install --upgrade pip
& $py -m pip install -e $qhfi

Write-Host "==> Installing backend dependencies" -ForegroundColor Cyan
& $py -m pip install -e $backend

Write-Host "==> Installing frontend dependencies" -ForegroundColor Cyan
Push-Location $frontend
npm install
Pop-Location

Write-Host "`nDone. Start the app with: ./scripts/dev.ps1" -ForegroundColor Green
