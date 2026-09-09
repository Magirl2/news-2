$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot "run_daily.ps1"
$TaskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""

$Tasks = @(
  @{ Name = "US Market Kakao Briefing 0900"; Schedule = "DAILY"; Time = "09:00" },
  @{ Name = "US Market Kakao Briefing 0930"; Schedule = "DAILY"; Time = "09:30" },
  @{ Name = "US Market Kakao Briefing 1000"; Schedule = "DAILY"; Time = "10:00" },
  @{ Name = "US Market Kakao Briefing Logon"; Schedule = "ONLOGON"; Time = "" }
)

foreach ($Task in $Tasks) {
  $Arguments = @(
    "/Create",
    "/TN", $Task.Name,
    "/SC", $Task.Schedule,
    "/TR", $TaskCommand,
    "/F"
  )

  if ($Task.Schedule -eq "DAILY") {
    $Arguments += @("/ST", $Task.Time)
  }

  & schtasks.exe @Arguments | Out-Host
  if ($LASTEXITCODE -ne 0) {
    if ($Task.Schedule -eq "ONLOGON") {
      Write-Host "Optional logon task was not created. Daily retry tasks are enough for normal morning delivery."
      continue
    }
    throw "예약 작업 생성 실패: $($Task.Name)"
  }
}

Write-Host "Windows daily retry tasks created."
Write-Host "- US Market Kakao Briefing 0900: 9:00 AM"
Write-Host "- US Market Kakao Briefing 0930: 9:30 AM retry"
Write-Host "- US Market Kakao Briefing 1000: 10:00 AM retry"
Write-Host "- US Market Kakao Briefing Logon: at Windows logon, if Windows allows it"
Write-Host "The bot sends each US market date only once, so retry triggers should not duplicate messages."
Write-Host "Task logs will be saved in logs\scheduled-task.log."
