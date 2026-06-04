@echo off
title Paper P&L audit (Track E)
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing
  pause
  exit /b 1
)
echo Snapshot only (no entry). For fill test add --try-entry during RTH.
.\.venv\Scripts\python.exe scripts\paper_pnl_audit.py %*
set RC=%ERRORLEVEL%
echo.
echo Report: Desktop\PAPER-PNL-AUDIT.txt
timeout /t 8
exit /b %RC%
