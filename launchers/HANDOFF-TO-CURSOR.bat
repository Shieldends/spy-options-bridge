@echo off
title Handoff fixes to Cursor
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
if "%~1"=="" (
  .\.venv\Scripts\python.exe scripts\cursor_handoff.py --drain-pending
) else (
  .\.venv\Scripts\python.exe scripts\cursor_handoff.py --bullet "%~1"
)
echo.
echo Sync: Projects\spy-hybrid-v3\sync\cursor_inbox.md
echo Paste: Desktop\CURSOR-LAST-HANDOFF.txt
timeout /t 5
