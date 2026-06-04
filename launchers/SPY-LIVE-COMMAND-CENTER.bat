@echo off
title SPY Live Command Center
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing at C:\Users\Shiel\spy-options-bridge
  pause
  exit /b 1
)
REM Launcher clears stale lock, shows error dialog (not silent black console)
.venv\Scripts\pythonw.exe scripts\cc_launcher.py 2>>"%USERPROFILE%\Desktop\COMMAND-CENTER-BOOT.txt"
if errorlevel 1 (
  .venv\Scripts\python.exe scripts\cc_launcher.py
  if errorlevel 1 pause
)
exit /b 0
