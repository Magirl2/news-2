$ErrorActionPreference = "Stop"

$TaskNames = @(
  "US Market Kakao Briefing",
  "US Market Kakao Briefing 0710",
  "US Market Kakao Briefing 0810",
  "US Market Kakao Briefing 0910",
  "US Market Kakao Briefing Logon"
)

$Removed = $false
foreach ($TaskName in $TaskNames) {
  $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($null -eq $Task) {
    continue
  }

  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Windows daily task removed: $TaskName"
  $Removed = $true
}

if (-not $Removed) {
  Write-Host "Windows daily tasks are already not registered."
}
