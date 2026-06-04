@echo off
title Clear Command Center GUI lock
del /f /q "C:\Users\Shiel\Desktop\SPY-CC-GUI.lock" 2>nul
echo GUI lock cleared. Now run SPY-LIVE-COMMAND-CENTER.bat once.
timeout /t 5
