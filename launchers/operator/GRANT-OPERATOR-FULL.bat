@echo off
setlocal
title Grant Operator FULL Session (60 min)
cd /d C:\Users\Shiel\spy-options-bridge
echo.
echo FULL session includes:
echo   - launch SPY .bat files (whitelist)
echo   - open Desktop / bridge folders
echo   - shell: pytest and bridge scripts (whitelist)
echo   - program: Cursor, Notepad (whitelist exe)
echo   - url: Alpaca paper, TradingView (browser)
echo.
echo NOT included (v2): mouse/keyboard UI automation (tier automate).
echo Services: DENIED until you add names to service_whitelist in YAML.
echo.
set /p CONFIRM=Grant operator FULL SESSION for 60 minutes? (Y/N):
if /i not "%CONFIRM%"=="Y" (
  echo Cancelled.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing. Run setup first.
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe scripts\operator_gateway.py --grant-tier session
if errorlevel 1 (
  echo FAIL: grant not written.
  pause
  exit /b 1
)
echo OK: C:\Users\Shiel\Desktop\OPERATOR-GRANT.json
echo Revoke anytime: delete that file.
pause
