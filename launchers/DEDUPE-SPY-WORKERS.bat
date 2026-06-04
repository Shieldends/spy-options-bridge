@echo off
title Dedupe supervisors only (safe during market)
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
REM Workers: use Command Center START TEAM / ARM — full dedupe can drop helpers mid-spawn
.\.venv\Scripts\python.exe scripts\dedupe_spy_workers.py --only command_center.py --only command_center_gui.py
pause
