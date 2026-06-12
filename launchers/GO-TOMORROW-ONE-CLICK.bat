@echo off
title GO TOMORROW — one click
color 1F
echo.
echo ============================================================
echo   GO TOMORROW — health + ARM (Alpaca PAPER)
echo ============================================================
echo.

powershell -NoProfile -Command "try { $h = Invoke-RestMethod -Uri 'https://spy-options-bridge.onrender.com/health' -TimeoutSec 30; Write-Host ('Bridge: ' + $h.version + ' ' + $h.broker_label); if ($h.version -lt '5.5.13') { Write-Host 'WARN: need 5.5.13+ for spread strategy — deploy Render first'; exit 1 } } catch { Write-Host 'Health FAIL'; exit 1 }"
if errorlevel 1 (
  echo Health check failed. Open Render and Manual Deploy, then run again.
  pause
  exit /b 1
)

call "C:\Users\Shiel\spy-options-bridge\launchers\ARM-FOR-OPEN-ONE-CLICK.bat" --skip-schtask
echo.
echo DONE. Leave TradingView open with both alerts ACTIVE.
echo Watch Alpaca Paper - Activities during session.
timeout /t 10
