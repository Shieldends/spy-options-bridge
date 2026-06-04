@echo off
setlocal
title Check email approvals (one-shot, safe)
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing.
  pause
  exit /b 1
)
echo One-shot IMAP poll — does NOT start team workers.
.\.venv\Scripts\python.exe scripts\email_command_listener.py --once
echo.
echo Grant:  C:\Users\Shiel\Desktop\OPERATOR-GRANT.json
echo Pending: C:\Users\Shiel\Desktop\PENDING-ACTION.json
pause
