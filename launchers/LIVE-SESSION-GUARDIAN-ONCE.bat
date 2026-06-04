@echo off
title SPY Guardian — one check
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
.\.venv\Scripts\python.exe scripts\live_session_guardian.py --once
timeout /t 6
exit /b 0
