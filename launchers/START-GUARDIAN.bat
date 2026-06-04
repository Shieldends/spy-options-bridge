@echo off
title Start Live Session Guardian
cd /d C:\Users\Shiel\spy-options-bridge
.\.venv\Scripts\python.exe scripts\cc_guardian_ctl.py start
timeout /t 5
exit /b 0
