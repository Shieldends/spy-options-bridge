@echo off
title SPY Live Session Guardian
set PYTHONUTF8=1
cd /d C:\Users\Shiel\spy-options-bridge
.\.venv\Scripts\pythonw.exe scripts\live_session_guardian.py
exit /b 0
