@echo off
title Stop blue PowerShell flashes (SPY helpers)
cd /d C:\Users\Shiel\spy-options-bridge
echo Stopping hidden console supervisors and deduping workers...
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'scripts'); import command_center as cc; print('supervisors ended:', cc.stop_stale_console_supervisors())"
.\.venv\Scripts\python.exe scripts\dedupe_spy_workers.py
echo.
echo OK — use LIVE-SESSION-GUARDIAN.bat for today (no GUI required).
echo Avoid: MONITOR bat in a loop, ARM while guardian runs, BURST-100 during MACD.
timeout /t 8
