# quantpy-stock-analysis

基于 qstock / AKShare 的 A 股选股、涨停策略扫描与参数优化工具集。

## 项目结构

```
quantpy-stock-analysis/
├── daily_advisor.py             # CLI 入口（每日顾问）
├── web_app.py                   # Web 入口（仪表盘）
├── quick_start_qstock.py        # 交互式菜单入口
├── quantpy/                     # 核心代码包
│   ├── paths.py                 # 统一路径（data / output / templates）
│   ├── stock_data.py            # 多数据源行情
│   ├── portfolio.py             # 个人持仓（超短+中线）
│   ├── sim_replay.py            # 模拟复盘
│   ├── ultra_short_scanner.py   # 超短扫描
│   ├── daily_advisor.py         # 日报逻辑
│   ├── web_app.py               # Flask 应用
│   └── ...
├── examples/                    # 演示与涨停扫描示例
├── scripts/                     # 定时任务与实验脚本
├── templates/dashboard.html     # Web 页面
├── data/portfolio_config.json   # 持仓配置
├── docs/                        # 使用指南
├── output/                      # 运行输出
└── requirements.txt
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 交互式菜单
python quick_start_qstock.py

# 每日顾问（超短捕捉 + 根据你的操作给建议）
python daily_advisor.py

# 仅超短扫描
python daily_advisor.py scan

# 录入一笔交易
python daily_advisor.py record

# 涨停策略扫描（示例脚本）
python examples/scan_limit_up_stocks.py

# 模拟复盘（20万 · 最多3仓 · 9:30-9:45选股 · 每5日复盘）
python daily_advisor.py sim --force
python daily_advisor.py sim-status

# Web 仪表盘（个人持仓 + 模拟持仓 + 一键操作）
python web_app.py
# 或
python daily_advisor.py web --port 5050

# 定时任务（Windows 计划任务，周一至周五自动执行）
powershell -ExecutionPolicy Bypass -File scripts/setup_scheduled_tasks.ps1
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase morning
```

## 核心模块

| 模块 | 说明 |
|------|------|
| `quantpy.stock_data` | 统一获取股票列表与历史 K 线，AKShare → qstock → 东方财富 → baostock 自动降级 |
| `quantpy.qstock_selector` | PE/PB/市值等多条件选股、持有期回测 |
| `quantpy.qstock_strategy_optimizer` | MA/RSI/MACD/KDJ 策略、涨停策略、参数优化 |
| `quantpy.ultra_short_scanner` | 超短评分：涨停、连板、高换手、放量、涨停不破开 |
| `quantpy.trade_journal` | 记录买卖，统计胜率/持仓习惯，生成个性化建议 |
| `quantpy.daily_advisor` | 每日报告：超短 TOP + 绩效复盘 + 优化建议 |
| `quantpy.sim_replay` | 模拟复盘：20万/3仓、9:45选股、止盈止损、每5日自动复盘 |
| `quantpy.portfolio` | 个人仓位：超短2万+中线15万、浮盈、仓位建议 |
| `quantpy.web_app` | 本地 Web 页：双持仓展示、超短TOP、日报、一键操作 |

## 模拟复盘

| 规则 | 说明 |
|------|------|
| 资金 | 20 万模拟账户 |
| 选股时间 | 9:30-9:45（可用 `--force` 非交易时段测试） |
| 最多持仓 | 3 只，均分可用现金 |
| 买入价 | 开盘价 +0.5% 估算（9:45 介入） |
| 止损/止盈 | -3% / +8%，最长持有 3 日 |
| 复盘 | 每 5 个交易日自动复盘并微调参数 |

早盘选股增强：理想高开 1-4%、排除急拉追高、叠加超短评分。

## 个人持仓配置

编辑 `data/portfolio_config.json` 设置超短/中线资金与持仓，修改后同步：

```bash
python daily_advisor.py portfolio --init
```

```json
{
  "ultra_short_capital": 20000,
  "midterm_capital": 150000,
  "total_capital": 170000,
  "positions": [
    {"code": "603379", "name": "三美股份", "quantity": 300, "cost_price": 66.5, "strategy": "中线"}
  ]
}
```

运行时数据在 `data/portfolio.json`（自动生成，勿提交 git）。

## 定时任务（Windows）

| 时间 | 任务 | 内容 |
|------|------|------|
| 周一至周五 09:35 | QuantPyStock-Morning | 刷新行情 + 模拟早盘选股 |
| 周一至周五 15:10 | QuantPyStock-Close | 采集收盘 + 模拟卖出检查 |
| 周一至周五 15:25 | QuantPyStock-Report | 每日报告 + 个人仓位 |

```powershell
# 一键注册计划任务
powershell -ExecutionPolicy Bypass -File scripts/setup_scheduled_tasks.ps1

# 手动测试
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase morning
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase close
powershell -ExecutionPolicy Bypass -File scripts/daily_runner.ps1 -Phase report

# 删除计划任务
powershell -ExecutionPolicy Bypass -File scripts/remove_scheduled_tasks.ps1
```

日志目录：`logs/daily_*.log`

## 学习功能

1. **录入交易**：`python daily_advisor.py record`（或批量导入 `data/trades_template.csv`）
2. **每日报告**：`python daily_advisor.py` 自动生成 `output/daily_reports/daily_report_YYYYMMDD.md`
3. **策略标签**：录入时选择 `超短` / `涨停` / `趋势` / `手动`，系统按策略分析胜率并给建议


## 编程示例

```python
from quantpy.stock_data import get_all_stocks, get_stock_hist
from quantpy.qstock_strategy_optimizer import StrategyOptimizer

optimizer = StrategyOptimizer()
stocks = get_all_stocks()
hits = optimizer.find_limit_up_stocks(stocks.head(50), lookback_days=10, max_workers=8)
print(hits)
```

## 文档

- [QSTOCK 使用指南](docs/QSTOCK_GUIDE.md)
- [涨停策略说明](docs/LIMIT_UP_STRATEGY_GUIDE.md)
- [股票列表获取指南](docs/GET_STOCKS_GUIDE.md)

## 免责声明

本工具仅供学习研究，不构成投资建议。股市有风险，投资需谨慎。
