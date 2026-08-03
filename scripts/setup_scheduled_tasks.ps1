# 注册 Windows 计划任务（A 股交易日自动执行）
# 可在当前用户下运行（无需管理员）

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runner = Join-Path $ProjectRoot "scripts\daily_runner.ps1"
$TaskPrefix = "QuantPyStock"

if (-not (Test-Path $Runner)) {
    Write-Error "找不到脚本: $Runner"
    exit 1
}

function Register-QuantPhase {
    param(
        [string]$Name,
        [string]$Phase,
        [string]$Time,
        [int]$TimeoutHours = 2
    )
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -Phase $Phase"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Time
    # 日报/三倍量可能超过默认时长；允许休眠唤醒、电池运行、错过补跑
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -WakeToRun `
        -ExecutionTimeLimit (New-TimeSpan -Hours $TimeoutHours) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName "$TaskPrefix-$Name" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Force | Out-Null
    Write-Host "已注册: $TaskPrefix-$Name  (周一至周五 $Time, 超时 ${TimeoutHours}h)"
}

Write-Host "项目目录: $ProjectRoot"
Write-Host "注册计划任务..."
Write-Host ""

# 早盘 9:35 - 刷新 + 模拟选股
Register-QuantPhase -Name "Morning" -Phase "morning" -Time "09:35" -TimeoutHours 1

# 尾盘 14:45 - 三倍量选股（此前常未注册导致「未运行」）
Register-QuantPhase -Name "TripleVolume" -Phase "triple-volume" -Time "14:45" -TimeoutHours 2

# 收盘 15:10 - 采集收盘 + 模拟卖出
Register-QuantPhase -Name "Close" -Phase "close" -Time "15:10" -TimeoutHours 1

# 收盘 15:25 - 完整日报 + 观察池（常跑 30~50 分钟）
Register-QuantPhase -Name "Report" -Phase "report" -Time "15:25" -TimeoutHours 2

Write-Host ""
Write-Host "当前任务:"
Get-ScheduledTask -TaskName "$TaskPrefix-*" | ForEach-Object {
    $info = Get-ScheduledTaskInfo $_
    Write-Host ("  {0,-28} State={1,-8} Last={2} Result={3}" -f $_.TaskName, $_.State, $info.LastRunTime, $info.LastTaskResult)
}
Write-Host ""
Write-Host "手动测试:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Runner`" -Phase morning"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Runner`" -Phase triple-volume"
Write-Host ""
Write-Host "删除全部: powershell -ExecutionPolicy Bypass -File `"$(Join-Path $ProjectRoot 'scripts\remove_scheduled_tasks.ps1')`""
