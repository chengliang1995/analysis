# 删除 QuantPy 相关计划任务（相位名单与 phases.json 对齐）
$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PhasesFile = Join-Path $ProjectRoot "scripts\phases.json"
$TaskPrefix = "QuantPyStock"
$names = @("Morning", "TripleVolume", "Close", "Report")

if (Test-Path $PhasesFile) {
    try {
        $cfg = Get-Content $PhasesFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.task_prefix) { $TaskPrefix = [string]$cfg.task_prefix }
        if ($cfg.phases) {
            $names = @()
            foreach ($p in $cfg.phases) {
                if ($p.task_name) { $names += [string]$p.task_name }
            }
        }
    } catch {
        Write-Warning "读取 phases.json 失败，使用默认任务名"
    }
}

foreach ($n in $names) {
    $taskName = "$TaskPrefix-$n"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "已删除: $taskName"
    } else {
        Write-Host "不存在: $taskName"
    }
}
