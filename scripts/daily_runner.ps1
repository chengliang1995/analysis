# 每日任务执行器（带日志 + 状态标记）
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("morning", "close", "report", "triple-volume", "all")]
    [string]$Phase
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
if ($Host.UI.RawUI) { chcp 65001 | Out-Null }

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
$DataDir = Join-Path $ProjectRoot "data"
$Advisor = Join-Path $ProjectRoot "daily_advisor.py"
$StatusFile = Join-Path $DataDir "scheduler_status.json"

# 抑制 py_mini_racer 等依赖的 UserWarning（避免 PowerShell 把 stderr 当异常）
$env:PYTHONWARNINGS = "ignore::UserWarning"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境: $Python"
    exit 1
}

New-Item -Type Directory -Force -Path $LogDir | Out-Null
New-Item -Type Directory -Force -Path $DataDir | Out-Null
$LogFile = Join-Path $LogDir ("daily_{0}_{1}.log" -f $Phase, (Get-Date -Format "yyyyMMdd_HHmmss"))
$script:StepFailures = 0

function Write-StepLine {
    param([string]$Text)
    Add-Content -Path $LogFile -Value $Text -Encoding UTF8
    Write-Host $Text
}

function Write-SchedulerStatus {
    param(
        [string]$PhaseName,
        [string]$State,   # running | ok | fail
        [string]$Message = ""
    )
    $all = @{}
    if (Test-Path $StatusFile) {
        try {
            $raw = Get-Content $StatusFile -Raw -Encoding UTF8
            $obj = $raw | ConvertFrom-Json
            foreach ($p in $obj.PSObject.Properties) {
                $entry = @{}
                foreach ($ep in $p.Value.PSObject.Properties) {
                    $entry[$ep.Name] = $ep.Value
                }
                $all[$p.Name] = $entry
            }
        } catch {
            $all = @{}
        }
    }
    $now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $prev = $all[$PhaseName]
    $started = $now
    if ($State -ne "running" -and $prev -and $prev["started_at"]) {
        $started = [string]$prev["started_at"]
    }
    $all[$PhaseName] = @{
        phase       = $PhaseName
        state       = $State
        started_at  = $started
        finished_at = if ($State -eq "running") { $null } else { $now }
        ok          = ($State -eq "ok")
        message     = $Message
        log         = [IO.Path]::GetFileName($LogFile)
        updated_at  = $now
    }
    ($all | ConvertTo-Json -Depth 5) | Set-Content -Path $StatusFile -Encoding UTF8
}

function Invoke-Step {
    param(
        [string]$Title,
        [string[]]$StepArgs
    )
    $line = "`n========== $Title ==========`n"
    Write-StepLine $line

    # 勿用 $Args 作参数名（与 PowerShell 内置变量冲突，会导致命令丢失并误跑 default=report）
    $output = & $Python -u $Advisor @StepArgs *>&1 | ForEach-Object {
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
        $script:StepFailures++
    }
}

Set-Location $ProjectRoot
Write-StepLine "Start $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-SchedulerStatus -PhaseName $Phase -State "running" -Message "started"

try {
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
            Invoke-Step "Triple volume watchlist" @("triple-volume-watch")
            Invoke-Step "Portfolio" @("portfolio")
        }
        "triple-volume" {
            # 计划任务在窗口边缘也可能偏几分钟，强制执行避免跳过
            Invoke-Step "Triple volume select" @("midterm-triple-volume", "--force")
        }
        "all" {
            & $PSCommandPath -Phase morning
            & $PSCommandPath -Phase triple-volume
            & $PSCommandPath -Phase close
            & $PSCommandPath -Phase report
            exit $LASTEXITCODE
        }
    }

    Write-StepLine "Done $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    if ($script:StepFailures -gt 0) {
        Write-StepLine "WARN: $script:StepFailures step(s) returned non-zero"
        Write-SchedulerStatus -PhaseName $Phase -State "fail" -Message "steps_failed=$script:StepFailures"
        Write-Host "`n日志: $LogFile"
        exit 1
    }
    Write-SchedulerStatus -PhaseName $Phase -State "ok" -Message "done"
    Write-Host "`n日志: $LogFile"
    exit 0
}
catch {
    Write-StepLine "ERROR: $($_.Exception.Message)"
    Write-SchedulerStatus -PhaseName $Phase -State "fail" -Message $_.Exception.Message
    Write-Host "`n日志: $LogFile"
    exit 1
}
