param(
    [int]$EveryMinutes = 10,
    [string]$TaskName = "PrideRadar"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runCmd = Join-Path $root "run.cmd"

if (-not (Test-Path -LiteralPath $runCmd)) {
    throw "run.cmd پیدا نشد: $runCmd"
}

$python = $env:PYTHON
if (-not $python) {
    $candidates = @(
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe",
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { $python = $c; break }
    }
}
if (-not $python) {
    throw "مفسر پایتون پیدا نشد. اول متغیر PYTHON را ست کن: `$env:PYTHON = 'C:\path\to\python.exe'"
}

$action = New-ScheduledTaskAction -Execute $runCmd -Argument "fetch" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "پایش خودکار آگهی‌های پراید دیوار در اهواز" `
    -Force | Out-Null

Write-Output "وظیفهٔ زمان‌بندی ثبت شد: $TaskName (هر $EveryMinutes دقیقه)"
Write-Output "اجرای دستی : schtasks /Run /TN $TaskName"
Write-Output "حذف        : schtasks /Delete /TN $TaskName /F"
