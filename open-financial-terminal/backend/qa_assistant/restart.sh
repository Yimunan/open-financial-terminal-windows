#!/usr/bin/env bash
# Durable restart of the QA backend on :8051 (PowerShell Start-Process children get reaped by the
# tool's job object; a disowned bash background process survives).
cd "$(dirname "$0")/.." || exit 1
# kill any uvicorn on 8051
for pid in $(powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*app.main*--port 8051*' } | ForEach-Object { \$_.ProcessId }" 2>/dev/null | tr -d '\r'); do
  powershell.exe -NoProfile -Command "Stop-Process -Id $pid -Force" 2>/dev/null
done
sleep 1
nohup .venv/Scripts/python.exe -m uvicorn app.main:app --port 8051 > qa_assistant/server.log 2>&1 &
disown 2>/dev/null
for i in $(seq 1 40); do curl -s -m 2 http://localhost:8051/api/health >/dev/null 2>&1 && { echo "UP after ${i}s"; exit 0; }; sleep 1; done
echo "FAILED to come up"; tail -n 20 qa_assistant/server.log; exit 1
