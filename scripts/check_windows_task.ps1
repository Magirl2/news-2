$ErrorActionPreference = "Stop"

$TaskNames = @(
  "US Market Kakao Briefing",
  "US Market Kakao Briefing 0710",
  "US Market Kakao Briefing 0810",
  "US Market Kakao Briefing 0910",
  "US Market Kakao Briefing Logon"
)

$AnyTask = $false
foreach ($TaskName in $TaskNames) {
  $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($null -eq $Task) {
    continue
  }

  $AnyTask = $true
  Write-Host "Windows task is registered: $TaskName"
  Write-Host "State: $($Task.State)"

  $Info = Get-ScheduledTaskInfo -TaskName $TaskName
  Write-Host "Last run time: $($Info.LastRunTime)"
  Write-Host "Last result: $($Info.LastTaskResult)"
  Write-Host "Next run time: $($Info.NextRunTime)"
  Write-Host ""
}

if (-not $AnyTask) {
  Write-Host "Windows daily task is not registered yet."
  Write-Host "Register it with: .\scripts\create_windows_task.ps1"
  exit 1
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StateFile = Join-Path $ProjectRoot "logs\send_state.json"
$TaskLog = Join-Path $ProjectRoot "logs\scheduled-task.log"

if (Test-Path $StateFile) {
  Write-Host "Send state: $StateFile"
  Get-Content -Path $StateFile -Tail 20
} else {
  Write-Host "Send state: not found yet"
}

if (Test-Path $TaskLog) {
  Write-Host "Recent task log:"
  Get-Content -Path $TaskLog -Tail 20
} else {
  Write-Host "Task log: not found yet"
}
