@echo off
title Push paper fill test (Track A)
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing
  pause
  exit /b 1
)
echo POST /exercise/entry via Render — use during RTH for best results.
.\.venv\Scripts\python.exe scripts\force_paper_fill_now.py
set RC=%ERRORLEVEL%
echo.
echo Result: Desktop\PAPER-FILL-TEST-RESULT.txt
timeout /t 8
exit /b %RC%
