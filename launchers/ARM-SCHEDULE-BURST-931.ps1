# SPY burst at ~9:31 ET — weekdays (scheduled or manual)
$ErrorActionPreference = "Stop"
$Root = "C:\Users\Shiel\spy-options-bridge"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Script = Join-Path $Root "scripts\burst_paper_fills.py"
$Log = "C:\Users\Shiel\Desktop\BURST-931-SCHEDULE.log"

function Write-Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts $msg"
    Add-Content -Path $Log -Value $line -Encoding UTF8
    Write-Host $line
}

if (-not (Test-Path $Py)) {
    Write-Log "FAIL: venv python missing at $Py"
    exit 1
}

Write-Log "START burst_paper_fills --wait-for-open --count 100 --batch-size 1"
& $Py $Script --wait-for-open --count 100 --batch-size 1 --after-open-sec 60
$code = $LASTEXITCODE
Write-Log "DONE exit=$code"
exit $code
