$ErrorActionPreference = 'Stop'
$launcher = 'C:\Users\Shiel\spy-options-bridge\launchers\SPY-LIVE-COMMAND-CENTER.bat'
$repo = 'C:\Users\Shiel\spy-options-bridge'
$icon = 'C:\Users\Shiel\spy-options-bridge\.venv\Scripts\python.exe,0'
$Wsh = New-Object -ComObject WScript.Shell
$desktops = @(
  [Environment]::GetFolderPath('Desktop'),
  'C:\Users\Shiel\Desktop',
  'C:\Users\Shiel\OneDrive\Desktop',
  'C:\Users\Public\Desktop'
) | Select-Object -Unique
foreach ($d in $desktops) {
  if (-not (Test-Path $d)) { Write-Output "SKIP $d"; continue }
  $lnk = Join-Path $d 'SPY Command Center.lnk'
  $sc = $Wsh.CreateShortcut($lnk)
  $sc.TargetPath = $launcher
  $sc.WorkingDirectory = $repo
  $sc.Description = 'SPY Team Command Center'
  $sc.IconLocation = $icon
  $sc.Save()
  Copy-Item -Path $launcher -Destination (Join-Path $d 'SPY Command Center.bat') -Force
  Write-Output "OK $d"
}
Write-Output '---REG_DESKTOP---'
(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders').Desktop
Write-Output '---SPY_FILES---'
foreach ($d in $desktops) {
  if (Test-Path $d) {
    Write-Output $d
    Get-ChildItem $d -Filter 'SPY Command Center*' | ForEach-Object { Write-Output ('  ' + $_.Name) }
  }
}
