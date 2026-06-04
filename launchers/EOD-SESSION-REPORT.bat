@echo off
title SPY EOD Session Report
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
.\.venv\Scripts\python.exe scripts\eod_session_report.py --email
echo.
echo Report on Desktop: EOD-SESSION-REPORT-*.txt
timeout /t 10
exit /b 0
