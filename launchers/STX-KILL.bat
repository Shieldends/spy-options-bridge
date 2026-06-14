@echo off
setlocal
title STX KILL — operator-gated flatten
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing. Run setup first.
  pause
  exit /b 1
)
REM Defaults to DRY-RUN (prints intended order only). Add --execute to arm live sending.
REM Either way it HALTS at a Y/Enter confirmation gate before any order is transmitted.
.\.venv\Scripts\python.exe scripts\stx_kill.py %*
echo.
pause
exit /b %ERRORLEVEL%
