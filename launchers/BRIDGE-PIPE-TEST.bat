@echo off
title Bridge pipe test — safe, no orders
color 1F
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe scripts\bridge_pipe_verify.py
set RC=%ERRORLEVEL%
if exist "%USERPROFILE%\Desktop\BRIDGE-PIPE-TEST.txt" start notepad "%USERPROFILE%\Desktop\BRIDGE-PIPE-TEST.txt"
exit /b %RC%
