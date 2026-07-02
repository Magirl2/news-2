$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
$TaskLog = Join-Path $LogDir "scheduled-task.log"

New-Item -ItemType Directory -Force $LogDir | Out-Null
Set-Location $ProjectRoot
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONIOENCODING = "utf-8"

$StartedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$StartedAt] Daily briefing task started." | Out-File -FilePath $TaskLog -Encoding utf8 -Append

try {
  $Output = & py -m market_briefing_bot send-once 2>&1
  $ExitCode = $LASTEXITCODE
  $Output | Out-File -FilePath $TaskLog -Encoding utf8 -Append
  if ($ExitCode -ne 0) {
    throw "Bot exited with code $ExitCode."
  }
  $FinishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  "[$FinishedAt] Daily briefing task finished." | Out-File -FilePath $TaskLog -Encoding utf8 -Append
}
catch {
  $FailedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  "[$FailedAt] Daily briefing task failed: $($_.Exception.Message)" | Out-File -FilePath $TaskLog -Encoding utf8 -Append
  throw
}
