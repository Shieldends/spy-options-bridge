@echo off
title SPY Monitor + READY status
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe scripts\team_monitor_ready.py
set RC=%ERRORLEVEL%
echo.
if %RC% EQU 0 (
  echo OK — team READY. See Desktop\SPY-Command-Center\WHAT-YOU-DO-NOW.txt
) else (
  echo NOT READY — run launchers\ARM-FOR-OPEN-ONE-CLICK.bat or GUI START TEAM
)
timeout /t 6
exit /b %RC%
