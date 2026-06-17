# qstock 选股与策略验证系统

基于 qstock 的完整股票选股、策略回测和参数优化系统。

## 📁 文件结构

```
quantpy-stock-analysis/
├── qstock_selector.py           # 基础选股功能
├── qstock_strategy_optimizer.py  # 策略优化功能
├── qstock_demo.py               # 完整演示脚本
├── quick_start_qstock.py        # 快速启动脚本
├── QSTOCK_GUIDE.md              # 详细使用指南
└── output/                      # 输出目录
    ├── selected_*.csv           # 选股结果
    ├── backtest_result.csv      # 回测结果
    └── technical_analysis.csv   # 技术分析数据
```

## 🚀 快速开始

### 方式1: 使用快速启动脚本（推荐）

```bash
# 交互式菜单
python quick_start_qstock.py

# 或直接运行特定功能
python quick_start_qstock.py select      # 快速选股
python quick_start_qstock.py backtest    # 快速回测
python quick_start_qstock.py technical   # 技术分析
python quick_start_qstock.py optimize    # 参数优化
```

### 方式2: 使用演示脚本

```bash
python qstock_demo.py
```

### 方式3: 编程方式

```python
from qstock_selector import QStockSelector

selector = QStockSelector()
stock_list = selector.get_all_stocks()
selected = selector.select_by_pe(stock_list, min_pe=10, max_pe=30)
selector.save_selection(selected, 'my_selection.csv')
```

## ✨ 核心功能

### 1. 基础选股 (QStockSelector)

- ✅ PE市盈率选股
- ✅ PB市净率选股
- ✅ 市值选股
- ✅ ROE选股
- ✅ 股价选股
- ✅ 换手率选股
- ✅ 多条件组合选股
- ✅ 策略回测验证
- ✅ 参数优化
- ✅ 选股报告生成

### 2. 技术指标 (StrategyOptimizer)

- ✅ 移动平均线 (MA)
- ✅ 相对强弱指标 (RSI)
- ✅ MACD指标
- ✅ 布林带 (BOLLINGER)
- ✅ KDJ指标

### 3. 交易策略

- ✅ 均线交叉策略
- ✅ RSI超卖超买策略
- ✅ MACD金叉死叉策略
- ✅ 布林带突破策略
- ✅ KDJ策略
- ✅ 多信号共振策略

### 4. 参数优化

- ✅ 均线周期优化
- ✅ RSI参数优化
- ✅ 回测验证
- ✅ 最优参数推荐

## 📊 使用场景

### 场景1: 价值股选股

```python
from qstock_selector import QStockSelector

selector = QStockSelector()
stock_list = selector.get_all_stocks()

# 价值股筛选
selected = selector.multi_filter_select(stock_list, {
    'pe': (10, 30),           # PE适中
    'pb': (1, 5),             # PB适中
    'market_cap': (50, 500)   # 中盘股
})

# 回测验证
backtest_result = selector.backtest_strategy(selected, hold_days=20)
```

### 场景2: 技术面选股

```python
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

optimizer = StrategyOptimizer()

# 获取数据
code = '600519'
hist_data = qs.get_data('hist', code, start='20250101', end='20260305')

# 多信号策略
df_signals = optimizer.multi_signal_strategy(
    hist_data,
    strategies=['MA_CROSS', 'RSI_BUY', 'MACD_GOLDEN'],
    min_signals=2
)

# 回测
backtest_result = optimizer.backtest_signal(df_signals, 'BUY', hold_days=20)
```

### 场景3: 策略参数优化

```python
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

optimizer = StrategyOptimizer()
hist_data = qs.get_data('hist', '000001', start='20250101', end='20260305')

# 优化均线参数
result = optimizer.optimize_ma_periods(
    hist_data,
    short_range=(5, 15),
    long_range=(20, 40),
    step=2
)

print(f"最优参数: MA{result['params']['short']}/MA{result['params']['long']}")
print(f"最优收益: {result['score']:.2f}%")
```

## 📈 输出说明

### 选股结果文件
- `selected_pe.csv` - PE选股结果
- `selected_pb.csv` - PB选股结果
- `selected_multi_filter.csv` - 多条件选股结果

### 回测结果文件
- `backtest_result.csv` - 单策略回测结果
- `multi_stock_backtest.csv` - 多股票回测结果
- `comprehensive_strategy_result.csv` - 综合策略结果

### 技术分析文件
- `technical_analysis.csv` - 技术指标数据

## 🔧 安装依赖

```bash
pip install qstock pandas numpy
```

## 📚 详细文档

完整使用指南请查看: [QSTOCK_GUIDE.md](QSTOCK_GUIDE.md)

## ⚠️ 免责声明

本工具仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。

## 📝 主要特性

- ✅ **简单易用**: 一键启动，交互式操作
- ✅ **功能完整**: 涵盖选股、回测、优化全流程
- ✅ **灵活配置**: 支持多种策略组合
- ✅ **结果可视化**: 自动生成报告
- ✅ **数据保存**: 结果自动保存为CSV

## 🎯 适合人群

- 股市初学者学习选股方法
- 量化交易爱好者研究策略
- 个人投资者辅助决策
- 金融专业学生实践学习

## 💡 使用建议

1. **先用小数据测试**: 先用少量股票测试策略
2. **多策略结合**: 基本面+技术面组合筛选
3. **充分回测**: 用历史数据验证策略有效性
4. **风险控制**: 设置止损，分散投资
5. **持续优化**: 根据市场变化调整参数

## 📞 技术支持

如有问题或建议，欢迎反馈。

---

**开始使用**: `python quick_start_qstock.py`

**查看指南**: 阅读 `QSTOCK_GUIDE.md`

**运行演示**: `python qstock_demo.py`
