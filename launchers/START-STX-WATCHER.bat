@echo off
setlocal
title STX WATCHER — background monitor (read-only)
cd /d C:\Users\Shiel\spy-options-bridge
if not exist ".venv\Scripts\pythonw.exe" (
  echo FAIL: .venv missing. Run setup first.
  pause
  exit /b 1
)
REM Read-only watcher. Edit --expiration / --strike / --type for the live STX leg.
REM Add --prev-close-iv 0.45 to enable the IV-spike abort trigger.
start "" .\.venv\Scripts\pythonw.exe scripts\stx_watcher.py --underlying STX --expiration 2026-05-15 --type put --strike 230 --poll 15
echo OK — STX watcher started (headless). State: backend-config\stx_watcher_state.json
timeout /t 5
exit /b 0
