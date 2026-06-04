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
.venv\Scripts\python.exe scripts\command_center_gui.py
if errorlevel 1 (
  echo Command Center exited with an error. See message above.
  pause
)
