# Restart the QA backend on :8051 with fresh code (no --reload; reload watcher was unreliable here).
$ErrorActionPreference = "SilentlyContinue"
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*app.main*--port 8051*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 1
$backend = Split-Path -Parent $PSScriptRoot
$py = Join-Path $backend ".venv\Scripts\python.exe"
Start-Process -FilePath $py -ArgumentList @("-m","uvicorn","app.main:app","--port","8051") `
  -WorkingDirectory $backend -RedirectStandardOutput (Join-Path $PSScriptRoot "server.log") `
  -RedirectStandardError (Join-Path $PSScriptRoot "server.err.log") -WindowStyle Hidden
Write-Host "restarting backend on :8051"
