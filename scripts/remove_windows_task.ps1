$ErrorActionPreference = "Stop"

$TaskName = "US Market Kakao Briefing"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $Task) {
  Write-Host "Windows daily task is already not registered: $TaskName"
  exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Windows daily task removed: $TaskName"
