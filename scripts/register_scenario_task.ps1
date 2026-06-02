# Registers weekday Scenario Lab at 6:35 AM local (= 9:35 AM Eastern if PC is Pacific)
$bat = "C:\Users\Shiel\Desktop\RUN-SCENARIO-LAB.bat"
$taskName = "SPY-Scenario-Lab"
$action = New-ScheduledTaskAction -Execute $bat
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "6:35AM"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Registered: $taskName at 6:35 AM Mon-Fri (adjust in Task Scheduler if your timezone differs)"
Write-Host "Runs: $bat"