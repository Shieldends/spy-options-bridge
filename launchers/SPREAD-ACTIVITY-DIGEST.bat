@echo off
title SPREAD ACTIVITY DIGEST
color 1F
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: .venv missing
  pause
  exit /b 1
)
.\.venv\Scripts\python.exe scripts\spread_activity_digest.py --no-open
if errorlevel 1 (
  echo.
  echo FAIL: digest script error
  pause
  exit /b 1
)
set "SIMPLE=%USERPROFILE%\Desktop\SPREAD-TODAY-SIMPLE.txt"
set "REPORT=%USERPROFILE%\Desktop\SPREAD-ACTIVITY-DIGEST.html"
echo.
echo   SIMPLE summary (read this): %SIMPLE%
echo.
start "" notepad "%SIMPLE%"
start "" "%REPORT%"
echo   Notepad SIMPLE + browser detail should stay OPEN.
echo   (This black box can close - Notepad stays.)
echo.
pause
