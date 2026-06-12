@echo off
title SPREAD ACTIVITY DIGEST
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe scripts\spread_activity_digest.py
echo.
echo Opened SPREAD-ACTIVITY-DIGEST.html on Desktop. Press F5 to refresh later.
timeout /t 8
