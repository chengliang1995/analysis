# 删除 QuantPy 相关计划任务
$TaskPrefix = "QuantPyStock"
$names = @("Morning", "Close", "Report")

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
