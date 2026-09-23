# 每日任务执行器（带日志 + 状态标记）
# 相位步骤单一数据源：scripts/phases.json
param(
    [Parameter(Mandatory = $true)]
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
$PhasesFile = Join-Path $ProjectRoot "scripts\phases.json"

# 抑制 py_mini_racer 等依赖的 UserWarning（避免 PowerShell 把 stderr 当异常）
$env:PYTHONWARNINGS = "ignore::UserWarning"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $Python)) {
    Write-Error "未找到虚拟环境: $Python"
    exit 1
}
if (-not (Test-Path $PhasesFile)) {
    Write-Error "找不到相位定义: $PhasesFile"
    exit 1
}

$phasesCfg = Get-Content $PhasesFile -Raw -Encoding UTF8 | ConvertFrom-Json
$phaseMap = @{}
foreach ($p in $phasesCfg.phases) {
    if ($p.id) {
        $phaseMap[[string]$p.id] = $p
    }
}

$known = @($phaseMap.Keys) + @("all")
if ($known -notcontains $Phase) {
    Write-Error ("未知相位: {0}；可选: {1}" -f $Phase, ($known -join ", "))
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

function Get-StepArgv {
    param($Step)
    # 优先 { "argv": ["cmd", ...] }，兼容旧版字符串/数组步骤
    if ($null -eq $Step) { return @() }
    if ($Step.PSObject -and ($Step.PSObject.Properties.Name -contains "argv")) {
        return @($Step.argv | ForEach-Object { [string]$_ })
    }
    if ($Step -is [string]) {
        return @($Step)
    }
    return @($Step | ForEach-Object { [string]$_ })
}

function Invoke-PhaseSteps {
    param([string]$PhaseId)

    $cfg = $phaseMap[$PhaseId]
    if (-not $cfg) {
        throw "相位未定义: $PhaseId"
    }
    $steps = @($cfg.steps)
    if (-not $steps -or $steps.Count -eq 0) {
        throw "相位无步骤: $PhaseId"
    }

    $idx = 0
    foreach ($step in $steps) {
        $idx++
        $argv = Get-StepArgv -Step $step
        if ($argv.Count -eq 0 -or [string]::IsNullOrWhiteSpace($argv[0])) {
            Write-StepLine "WARN: 跳过空步骤 #$idx"
            continue
        }
        $title = "[{0}/{1}] {2}" -f $idx, $steps.Count, ($argv -join " ")
        Invoke-Step -Title $title -StepArgs $argv
    }
}

Set-Location $ProjectRoot
Write-StepLine "Start $Phase at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-SchedulerStatus -PhaseName $Phase -State "running" -Message "started"

try {
    if ($Phase -eq "all") {
        foreach ($pid in @($phasesCfg.phases | ForEach-Object { [string]$_.id })) {
            & $PSCommandPath -Phase $pid
            if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
                exit $LASTEXITCODE
            }
        }
        exit 0
    }

    Invoke-PhaseSteps -PhaseId $Phase

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
