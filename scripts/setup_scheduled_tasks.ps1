# 注册 Windows 计划任务（A 股交易日自动执行）
# 相位定义单一数据源：scripts/phases.json

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runner = Join-Path $ProjectRoot "scripts\daily_runner.ps1"
$PhasesFile = Join-Path $ProjectRoot "scripts\phases.json"
$TaskPrefix = "QuantPyStock"

if (-not (Test-Path $Runner)) {
    Write-Error "找不到脚本: $Runner"
    exit 1
}
if (-not (Test-Path $PhasesFile)) {
    Write-Error "找不到相位定义: $PhasesFile"
    exit 1
}

$phasesCfg = Get-Content $PhasesFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($phasesCfg.task_prefix) {
    $TaskPrefix = [string]$phasesCfg.task_prefix
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
Write-Host "相位定义: $PhasesFile"
Write-Host "注册计划任务..."
Write-Host ""

foreach ($p in $phasesCfg.phases) {
    $id = [string]$p.id
    $name = if ($p.task_name) { [string]$p.task_name } else { $id }
    $time = [string]$p.time
    $timeout = if ($null -ne $p.timeout_hours) { [int]$p.timeout_hours } else { 2 }
    if (-not $id -or -not $time) {
        Write-Warning "跳过无效相位条目"
        continue
    }
    Register-QuantPhase -Name $name -Phase $id -Time $time -TimeoutHours $timeout
}

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
