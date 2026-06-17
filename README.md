# quantpy-stock-analysis

基于 qstock / AKShare 的 A 股选股、涨停策略扫描与参数优化工具集。

## 项目结构

```
quantpy-stock-analysis/
├── stock_data.py                # 统一数据获取（多数据源降级）
├── qstock_selector.py           # 基本面选股与回测
├── qstock_strategy_optimizer.py # 技术指标、涨停策略、参数优化
├── quick_start_qstock.py        # 交互式快速入口
├── qstock_demo.py               # 完整功能演示
├── scan_limit_up_stocks.py      # 全市场涨停策略扫描
├── scan_limit_up_simple.py      # 指定股票列表快速扫描
├── scripts/                     # 实验脚本与诊断工具
├── docs/                        # 使用指南
├── output/                      # 运行结果输出
└── requirements.txt
```

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 交互式菜单
python quick_start_qstock.py

# 涨停策略扫描（全市场，并发加速）
python scan_limit_up_stocks.py

# 指定股票快速扫描
python scan_limit_up_simple.py
```

## 核心模块

| 模块 | 说明 |
|------|------|
| `stock_data.py` | 统一获取股票列表与历史 K 线，AKShare → qstock → 东方财富 → baostock 自动降级 |
| `qstock_selector.py` | PE/PB/市值等多条件选股、持有期回测 |
| `qstock_strategy_optimizer.py` | MA/RSI/MACD/KDJ 策略、涨停策略、参数优化 |

## 编程示例

```python
from stock_data import get_all_stocks, get_stock_hist
from qstock_strategy_optimizer import StrategyOptimizer

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
