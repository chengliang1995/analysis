# 脚本目录

## 定时任务（推荐）

| 脚本 | 说明 |
|------|------|
| `setup_scheduled_tasks.ps1` | 注册 Windows 计划任务（早盘/收盘/日报） |
| `remove_scheduled_tasks.ps1` | 删除计划任务 |
| `daily_runner.ps1` | 任务执行器（morning / close / report） |
| `run_daily_morning.bat` | 手动运行早盘任务 |
| `run_daily_close.bat` | 手动运行收盘任务 |

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_scheduled_tasks.ps1
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase morning
```

## 实验与诊断

| 脚本 | 说明 |
|------|------|
| `get_stocks_*.py` | 各数据源实验（已整合到 `stock_data.py`） |
| `test_*.py` / `diagnose_qstock.py` | 连通性诊断 |
| `run_test.bat` | Windows 批量测试 |

## 推荐入口

```bash
python quick_start_qstock.py
python web_app.py
python daily_advisor.py
python scan_limit_up_stocks.py
```
