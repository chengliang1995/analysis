# 每日任务执行器（带日志）
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "close", "report", "all")]
    [string]$Phase
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($Host.UI.RawUI) { chcp 65001 | Out-Null }

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$Advisor = Join-Path $ProjectRoot "daily_advisor.py"

# 抑制 py_mini_racer 等依赖的 UserWarning（避免 PowerShell 把 stderr 当异常）
$env:PYTHONWARNINGS = "ignore::UserWarning"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境: $Python"
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("daily_{0}_{1}.log" -f $Phase, (Get-Date -Format "yyyyMMdd_HHmmss"))

function Write-StepLine {
    param([string]$Text)
    Add-Content -Path $LogFile -Value $Text -Encoding UTF8
    Write-Host $Text
}

function Invoke-Step {
    param([string]$Title, [string[]]$Args)
    $line = "`n========== $Title ==========`n"
    Write-StepLine $line

    # *>&1 合并输出；ErrorRecord 转字符串，避免 NativeCommandError 红字报错
    $output = & $Python $Advisor @Args *>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            $_.Exception.Message
        } else {
            $_
        }
    }
    if ($output) {
        $output | ForEach-Object { Write-StepLine $_ }
    }

    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        Write-StepLine "WARN: exit code $LASTEXITCODE"
    }
}

Set-Location $ProjectRoot
Write-StepLine "Start $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

switch ($Phase) {
    "morning" {
        Invoke-Step "Refresh quotes" @("refresh")
        Invoke-Step "Sim morning select" @("sim")
    }
    "close" {
        Invoke-Step "Collect close" @("refresh")
        Invoke-Step "Sim exit check" @("sim")
        Invoke-Step "Sim status" @("sim-status")
    }
    "report" {
        Invoke-Step "Daily report" @("report", "--prefilter", "300", "--min-score", "35")
        Invoke-Step "Portfolio" @("portfolio")
    }
    "all" {
        & $PSCommandPath -Phase morning
        & $PSCommandPath -Phase close
        & $PSCommandPath -Phase report
        exit $LASTEXITCODE
    }
}

Write-StepLine "Done $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "`n日志: $LogFile"
