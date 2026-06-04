@echo off
title Stop blue/black console flashes
cd /d C:\Users\Shiel\spy-options-bridge
.\.venv\Scripts\pythonw.exe scripts\stop_console_flashes.py
if errorlevel 1 .\.venv\Scripts\python.exe scripts\stop_console_flashes.py
echo See Desktop\STOP-CONSOLE-FLASHES.txt
timeout /t 6
