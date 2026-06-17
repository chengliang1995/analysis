# 实验与诊断脚本

本目录存放开发过程中的实验脚本，核心功能请使用根目录模块：

| 脚本 | 说明 |
|------|------|
| `get_stocks_*.py` | 各数据源获取股票列表的实验版本（已整合到 `stock_data.py`） |
| `get_all_a_stocks.py` | 批量获取 A 股列表与历史数据 |
| `test_*.py` / `diagnose_qstock.py` | qstock 连通性与 API 诊断 |
| `run_test.bat` | Windows 批量测试入口 |

推荐入口：

```bash
python quick_start_qstock.py
python scan_limit_up_stocks.py
python scan_limit_up_simple.py
```
