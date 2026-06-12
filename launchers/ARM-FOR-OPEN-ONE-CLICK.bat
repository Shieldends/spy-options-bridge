@echo off
setlocal
title ARM FOR OPEN — one click
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing. Run setup first.
  pause
  exit /b 1
)
REM Creates auto-arm marker on first run; 5s countdown then auto-Y
.\.venv\Scripts\python.exe scripts\arm_for_open.py --yes --skip-schtask --create-auto-arm-marker %*
set RC=%ERRORLEVEL%
if %RC% NEQ 0 (
  echo ARM cancelled or failed — see Desktop\ARM-FOR-OPEN.log
  pause
  exit /b %RC%
)
echo.
echo OK — armed. Log: Desktop\ARM-FOR-OPEN.log
echo STOP: Command Center STOP ALL + delete OPERATOR-GRANT.json to revoke operator.
timeout /t 8
exit /b 0
