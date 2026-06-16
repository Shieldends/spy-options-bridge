@echo off
title Local Bridge — Put Walker Path 2
cd /d C:\Users\Shiel\spy-options-bridge
echo.
echo   Local bridge on http://127.0.0.1:8765
echo   STX webhook: http://127.0.0.1:8765/webhook/stx-close
echo   Put walker replaced by Multi-Strike Sniper Grid (v5.5.26)
echo.
.\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8765
