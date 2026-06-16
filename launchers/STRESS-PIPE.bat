@echo off
title STRESS PIPE — paper flood test
color 4F
echo.
echo ============================================================
echo   STRESS PIPE — paper open/close flood (pause TV alerts first)
echo ============================================================
echo.
echo   Pause BOTH SPY alerts in TradingView before continuing:
echo     1. Bull Put Spread ENTRY
echo     2. Bull Put Spread WARNING
echo.
pause
echo.

cd /d "C:\Users\Shiel\spy-options-bridge"
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing — run from spy-options-bridge folder setup first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" scripts\stress_pipe.py %*
set RC=%ERRORLEVEL%
echo.
if %RC%==0 (
  echo STRESS PIPE finished — check STRESS-PIPE-LOG.txt on Desktop
) else (
  echo STRESS PIPE ended with errors — see log
)
echo.
pause
exit /b %RC%
