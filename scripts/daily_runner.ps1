# 每日任务执行器（带日志）
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "close", "report", "all")]
    [string]$Phase
)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$Advisor = Join-Path $ProjectRoot "daily_advisor.py"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境: $Python"
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("daily_{0}_{1}.log" -f $Phase, (Get-Date -Format "yyyyMMdd_HHmmss"))

function Invoke-Step {
    param([string]$Title, [string[]]$Args)
    $line = "`n========== $Title ==========`n"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
    & $Python $Advisor @Args 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        Add-Content -Path $LogFile -Value "WARN: exit code $LASTEXITCODE" -Encoding UTF8
    }
}

Set-Location $ProjectRoot
Add-Content -Path $LogFile -Value "Start $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding UTF8

switch ($Phase) {
    "morning" {
        # 9:30-9:45 早盘：刷新行情 + 模拟选股
        Invoke-Step "刷新行情" @("refresh")
        Invoke-Step "模拟复盘-早盘选股" @("sim")
    }
    "close" {
        # 收盘后：刷新收盘 + 模拟卖出检查
        Invoke-Step "采集收盘" @("refresh")
        Invoke-Step "模拟复盘-卖出检查" @("sim")
        Invoke-Step "模拟账户状态" @("sim-status")
    }
    "report" {
        # 完整日报
        Invoke-Step "每日顾问报告" @("report", "--prefilter", "300", "--min-score", "35")
        Invoke-Step "个人仓位" @("portfolio")
    }
    "all" {
        & $PSCommandPath -Phase morning
        & $PSCommandPath -Phase close
        & $PSCommandPath -Phase report
        exit $LASTEXITCODE
    }
}

Add-Content -Path $LogFile -Value "Done $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -Encoding UTF8
Write-Host "`n日志: $LogFile"
