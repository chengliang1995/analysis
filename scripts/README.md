# 脚本目录

## 定时任务（推荐）

相位定义单一数据源：`scripts/phases.json`（注册脚本与仪表盘状态共用）。

| 脚本 | 说明 |
|------|------|
| `phases.json` | 相位名 / 计划时刻 / 超时 / CLI 步骤（`steps[].argv`） |
| `setup_scheduled_tasks.ps1` | 按 phases.json 注册 Windows 计划任务（含 Morning / Ma20Am / Ma20Pm / TripleVolume / Close / Report） |
| `remove_scheduled_tasks.ps1` | 删除计划任务 |
| `daily_runner.ps1` | 按 phases.json 执行步骤；早盘：**短线 → 卡脖子 → 超短 → 中线**（短线/卡脖子优先防超时） |
| `run_daily_morning.bat` | 手动运行早盘任务 |
| `run_daily_close.bat` | 手动运行收盘任务 |

更完整的「实盘 / 模拟 / 回测一致性与调参」约定见 [docs/trustworthy-pipeline.md](../docs/trustworthy-pipeline.md)。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_scheduled_tasks.ps1
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase morning
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase ma20-am
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase ma20-pm
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase triple-volume
```

## 实验与诊断（不进入定时任务）

以下为历史数据源/连通性实验脚本，**不要**注册进计划任务；正式选股与行情请走 `quantpy/` 与 `daily_advisor.py`。

| 脚本 | 说明 |
|------|------|
| `get_stocks_*.py` | 各数据源实验（已整合到 `quantpy/stock_data.py`） |
| `test_*.py` / `diagnose_qstock.py` | 连通性诊断 |
| `run_test.bat` | Windows 批量测试 |

## 推荐入口

```bash
python quick_start_qstock.py
python web_app.py
python daily_advisor.py
python examples/scan_limit_up_stocks.py
```
