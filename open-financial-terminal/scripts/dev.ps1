# Start the backend (FastAPI :8000) and frontend (Vite :5173) in separate windows.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$uvicorn = Join-Path $backend ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path $uvicorn)) {
    throw "Backend venv not found. Run ./scripts/setup.ps1 first."
}

Write-Host "==> Backend  -> http://localhost:8050  (docs: /docs)" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$backend'; & '$uvicorn' app.main:app --reload --port 8050"
)

Write-Host "==> Frontend -> http://localhost:5173" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$frontend'; npm run dev"
)

Write-Host "`nBoth processes launched in new windows. Open http://localhost:5173" -ForegroundColor Green
