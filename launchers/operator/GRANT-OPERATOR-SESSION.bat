@echo off
setlocal
title Grant Operator Session (60 min)
cd /d C:\Users\Shiel\spy-options-bridge
set /p CONFIRM=Grant operator SESSION (launch+shell) for 60 minutes? (Y/N):
if /i not "%CONFIRM%"=="Y" (
  echo Cancelled.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing. Run setup first.
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe scripts\operator_gateway.py --grant-tier session
if errorlevel 1 (
  echo FAIL: grant not written.
  pause
  exit /b 1
)
echo OK: C:\Users\Shiel\Desktop\OPERATOR-GRANT.json
echo Revoke anytime: delete that file.
pause
