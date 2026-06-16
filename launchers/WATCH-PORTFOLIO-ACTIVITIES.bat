@echo off
title WATCH — Portfolio + Activities
color 1F
cd /d C:\Users\Shiel\spy-options-bridge
set "SIMPLE=%USERPROFILE%\Desktop\SPREAD-TODAY-SIMPLE.txt"
set "REPORT=%USERPROFILE%\Desktop\SPREAD-ACTIVITY-DIGEST.html"
set "ALPACA_HOME=https://app.alpaca.markets/paper/dashboard/overview"
set "ALPACA_POS=https://app.alpaca.markets/paper/dashboard/positions"
set "ALPACA_ACT=https://app.alpaca.markets/paper/dashboard/account/activities"

echo.
echo   Refreshing today summary, then opening Alpaca + digest...
echo.

if exist ".venv\Scripts\python.exe" (
  .\.venv\Scripts\python.exe scripts\spread_activity_digest.py --no-open
) else (
  echo WARN: digest skipped — .venv missing
)

if exist "%SIMPLE%" start "" notepad "%SIMPLE%"
if exist "%REPORT%" start "" "%REPORT%"
start "" "%ALPACA_HOME%"
start "" "%ALPACA_POS%"
start "" "%ALPACA_ACT%"

echo.
echo   OPEN: Notepad summary + digest browser + Alpaca portfolio/positions/activities
echo.
timeout /t 8
