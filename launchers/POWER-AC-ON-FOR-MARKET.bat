@echo off
REM SPY pre-open: keep PC awake on AC (does not disable hibernate)
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 30
powercfg /change disk-timeout-ac 0
echo AC power: sleep NEVER, display 30 min, disk NEVER.
echo See Desktop\SPY-Command-Center\POWER-KEEP-ON.txt for revert steps.
pause

