@echo off
setlocal
title DO NOW LAUNCH - deploy v5.5.9
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing.
  pause
  exit /b 1
)
set PENDING=C:\Users\Shiel\Desktop\PENDING-ACTION.json
findstr /C:"\"status\": \"pending\"" "%PENDING%" >nul 2>&1
if %ERRORLEVEL% equ 0 (
  echo.
  echo REPLY YES FIRST - shieldinc850@gmail.com
  echo Subject: [SPY Command Center] NEED APPROVAL - Deploy v5.5.9 to Render
  echo Then optional: C:\Users\Shiel\Desktop\SPY-Command-Center\CHECK-EMAIL-APPROVALS.bat
  echo.
) else (
  echo No pending approval on file - sending NEED APPROVAL email...
  .venv\Scripts\python.exe launchers\_do_now_send_approval.py
)
echo Opening Render dashboard (no credentials stored here)...
start "" "https://dashboard.render.com/"
timeout /t 6
exit /b 0
