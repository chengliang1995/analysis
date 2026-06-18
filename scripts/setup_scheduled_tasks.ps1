# 注册 Windows 计划任务（A 股交易日自动执行）
# 请以管理员身份运行 PowerShell，或在当前用户下运行（无需管理员）

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runner = Join-Path $ProjectRoot "scripts\daily_runner.ps1"
$TaskPrefix = "QuantPyStock"

if (-not (Test-Path $Runner)) {
    Write-Error "找不到脚本: $Runner"
    exit 1
}

$ActionTemplate = {
    param($Name, $Phase, $Time)
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Phase $Phase"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Time
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName "$TaskPrefix-$Name" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "已注册: $TaskPrefix-$Name  (周一至周五 $Time)"
}

Write-Host "项目目录: $ProjectRoot"
Write-Host "注册计划任务..."
Write-Host ""

# 早盘 9:35 - 刷新 + 模拟选股
& $ActionTemplate "Morning" "morning" "09:35"

# 收盘 15:10 - 采集收盘 + 模拟卖出
& $ActionTemplate "Close" "close" "15:10"

# 收盘 15:25 - 完整日报 + 仓位
& $ActionTemplate "Report" "report" "15:25"

Write-Host ""
Write-Host "完成。可在「任务计划程序」中查看任务前缀: $TaskPrefix-*"
Write-Host ""
Write-Host "手动测试:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Runner`" -Phase morning"
Write-Host ""
Write-Host "删除任务:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskPrefix-Morning' -Confirm:`$false"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskPrefix-Close' -Confirm:`$false"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskPrefix-Report' -Confirm:`$false"
