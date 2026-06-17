# qstock 选股与策略验证系统使用指南

## 目录
- [安装](#安装)
- [快速开始](#快速开始)
- [功能说明](#功能说明)
- [使用示例](#使用示例)
- [API文档](#api文档)

---

## 安装

### 1. 安装 qstock

```bash
pip install qstock
```

### 2. 验证安装

```python
import qstock as qs
print(qs.__version__)
```

---

## 快速开始

### 方式1: 使用交互式演示脚本

```bash
python qstock_demo.py
```

按提示选择演示功能：
1. 基础选股演示
2. 多条件组合选股
3. 技术指标策略
4. 策略参数优化
5. 多股票回测
6. 综合策略演示

### 方式2: 编程方式使用

```python
from qstock_selector import QStockSelector

# 创建选股器
selector = QStockSelector()

# 获取股票列表
stock_list = selector.get_all_stocks()

# PE选股
selected = selector.select_by_pe(stock_list, min_pe=10, max_pe=30)

# 保存结果
selector.save_selection(selected, 'my_selection.csv')
```

---

## 功能说明

### 1. 基础选股功能 (QStockSelector)

#### 获取股票数据
- `get_all_stocks()` - 获取所有A股基本信息
- `get_stock_realtime(codes)` - 获取实时行情
- `get_stock_hist(code, start, end)` - 获取历史数据
- `get_stock_fundamental(codes)` - 获取基本面数据

#### 选股策略
- `select_by_pe(stock_list, min_pe, max_pe)` - 按市盈率选股
- `select_by_pb(stock_list, min_pb, max_pb)` - 按市净率选股
- `select_by_market_cap(stock_list, min_cap, max_cap)` - 按市值选股
- `select_by_roe(stock_list, min_roe)` - 按ROE选股
- `select_by_price(stock_list, min_price, max_price)` - 按股价选股
- `select_by_turnover(stock_list, min_turnover, max_turnover)` - 按换手率选股
- `multi_filter_select(stock_list, filters)` - 多条件组合选股

#### 策略验证
- `backtest_strategy(selected_stocks, hold_days)` - 简单持有期回测
- `optimize_parameters(stock_list, strategy_func, param_ranges)` - 参数优化
- `generate_report(selected_stocks, title)` - 生成选股报告
- `save_selection(selected_stocks, filename)` - 保存选股结果

### 2. 策略优化功能 (StrategyOptimizer)

#### 技术指标计算
- `calculate_ma(df, periods)` - 计算移动平均线
- `calculate_rsi(df, period)` - 计算RSI相对强弱指标
- `calculate_macd(df, fast, slow, signal)` - 计算MACD指标
- `calculate_bollinger(df, period, std_dev)` - 计算布林带
- `calculate_kdj(df, n, m1, m2)` - 计算KDJ指标

#### 策略信号
- `ma_cross_strategy(df, short_period, long_period)` - 均线交叉策略
- `rsi_strategy(df, oversold, overbought)` - RSI超卖超买策略
- `macd_strategy(df)` - MACD金叉死叉策略
- `bollinger_strategy(df)` - 布林带突破策略
- `kdj_strategy(df, oversold, overbought)` - KDJ策略

#### 参数优化
- `optimize_ma_periods(df, short_range, long_range, step)` - 优化均线参数
- `optimize_rsi_params(df, oversold_range, overbought_range, step)` - 优化RSI参数

#### 综合策略
- `multi_signal_strategy(df, strategies, min_signals)` - 多信号共振策略

---

## 使用示例

### 示例1: 简单的PE选股

```python
from qstock_selector import QStockSelector

selector = QStockSelector()

# 获取股票列表
stock_list = selector.get_all_stocks()

# PE选股：市盈率10-30倍
selected = selector.select_by_pe(stock_list, min_pe=10, max_pe=30)

# 查看结果
print(selected.head(10))

# 保存结果
selector.save_selection(selected, 'low_pe_stocks.csv')
```

### 示例2: 多条件组合选股

```python
from qstock_selector import QStockSelector

selector = QStockSelector()

stock_list = selector.get_all_stocks()

# 定义筛选条件
filters = {
    'pe': (10, 30),          # PE 10-30倍
    'pb': (1, 5),            # PB 1-5倍
    'market_cap': (50, 500)  # 市值50-500亿
}

# 多条件选股
selected = selector.multi_filter_select(stock_list, filters)

# 生成报告
selector.generate_report(selected, "价值股选股")

# 回测验证
backtest_result = selector.backtest_strategy(selected, hold_days=20)
```

### 示例3: 技术指标选股

```python
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

optimizer = StrategyOptimizer()

# 获取股票历史数据
code = '600519'
hist_data = qs.get_data('hist', code, start='20250101', end='20260305')

# 计算技术指标
hist_with_indicators = optimizer.calculate_ma(hist_data, [5, 10, 20])
hist_with_rsi = optimizer.calculate_rsi(hist_with_indicators, period=14)

# 应用RSI策略
df_with_signals = optimizer.rsi_strategy(hist_with_rsi, oversold=30, overbought=70)

# 回测RSI买入信号
backtest_result = optimizer.backtest_signal(df_with_signals, 'RSI_BUY', hold_days=20)
```

### 示例4: 参数优化

```python
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

optimizer = StrategyOptimizer()

# 获取数据用于优化
code = '000001'
hist_data = qs.get_data('hist', code, start='20250101', end='20260305')

# 优化均线参数
result = optimizer.optimize_ma_periods(
    hist_data,
    short_range=(5, 15),
    long_range=(20, 40),
    step=2
)

print(f"最优参数: {result['params']}")
print(f"最优收益: {result['score']:.2f}%")
```

### 示例5: 多信号共振策略

```python
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

optimizer = StrategyOptimizer()

# 获取数据
code = '600519'
hist_data = qs.get_data('hist', code, start='20250101', end='20260305')

# 多信号策略：同时满足至少2个信号
df_with_signals = optimizer.multi_signal_strategy(
    hist_data,
    strategies=['MA_CROSS', 'RSI_BUY', 'MACD_GOLDEN'],
    min_signals=2
)

# 回测综合信号
backtest_result = optimizer.backtest_signal(df_with_signals, 'BUY', hold_days=20)
```

### 示例6: 综合选股策略

```python
from qstock_selector import QStockSelector
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

selector = QStockSelector()
optimizer = StrategyOptimizer()

# 步骤1: 基本面筛选
stock_list = selector.get_all_stocks()
selected_basic = selector.multi_filter_select(stock_list, {
    'pe': (10, 30),
    'market_cap': (50, 500)
})

# 步骤2: 技术面筛选
final_stocks = []
start_date = '20250101'
end_date = '20260305'

for idx, stock in selected_basic.head(20).iterrows():
    code = stock['code']

    try:
        hist_data = qs.get_data('hist', code, start=start_date, end=end_date)

        # 应用技术策略
        df_signals = optimizer.multi_signal_strategy(
            hist_data,
            strategies=['MA_CROSS', 'RSI_BUY'],
            min_signals=1
        )

        # 检查最新信号
        if df_signals.iloc[-1]['BUY'] == 1:
            final_stocks.append(stock)

    except:
        continue

print(f"基本面+技术面双重筛选: {len(final_stocks)} 只股票")

# 步骤3: 回测验证
if final_stocks:
    import pandas as pd
    final_df = pd.DataFrame(final_stocks)
    backtest_result = selector.backtest_strategy(final_df, hold_days=20)
```

---

## API文档

### QStockSelector

#### 构造函数
```python
QStockSelector()
```

#### 方法

##### get_all_stocks()
获取所有A股股票基本信息

**返回值:** pandas.DataFrame

##### get_stock_realtime(codes)
获取股票实时行情

**参数:**
- `codes` (List[str]): 股票代码列表

**返回值:** pandas.DataFrame

##### get_stock_hist(code, start, end, freq='daily')
获取历史行情数据

**参数:**
- `code` (str): 股票代码
- `start` (str): 开始日期 YYYYMMDD
- `end` (str): 结束日期 YYYYMMDD
- `freq` (str): 频率 daily/weekly/monthly

**返回值:** pandas.DataFrame

##### select_by_pe(stock_list, min_pe, max_pe)
根据PE市盈率选股

**参数:**
- `stock_list` (DataFrame): 股票列表
- `min_pe` (float): 最小PE
- `max_pe` (float): 最大PE

**返回值:** pandas.DataFrame

##### multi_filter_select(stock_list, filters)
多条件组合选股

**参数:**
- `stock_list` (DataFrame): 股票列表
- `filters` (Dict): 筛选条件字典

**返回值:** pandas.DataFrame

##### backtest_strategy(selected_stocks, hold_days)
简单的持有期回测

**参数:**
- `selected_stocks` (DataFrame): 选中的股票
- `hold_days` (int): 持有天数

**返回值:** pandas.DataFrame

### StrategyOptimizer

#### 构造函数
```python
StrategyOptimizer()
```

#### 方法

##### calculate_ma(df, periods=[5, 10, 20, 60])
计算移动平均线

**参数:**
- `df` (DataFrame): 价格数据，需包含 'close' 列
- `periods` (List[int]): 周期列表

**返回值:** pandas.DataFrame

##### calculate_rsi(df, period=14)
计算RSI相对强弱指标

**参数:**
- `df` (DataFrame): 价格数据
- `period` (int): 周期

**返回值:** pandas.DataFrame

##### calculate_macd(df, fast=12, slow=26, signal=9)
计算MACD指标

**参数:**
- `df` (DataFrame): 价格数据
- `fast` (int): 快线周期
- `slow` (int): 慢线周期
- `signal` (int): 信号线周期

**返回值:** pandas.DataFrame

##### ma_cross_strategy(df, short_period, long_period)
均线交叉选股策略

**参数:**
- `df` (DataFrame): 价格数据
- `short_period` (int): 短期均线
- `long_period` (int): 长期均线

**返回值:** pandas.DataFrame (包含MA_CROSS信号列)

##### optimize_ma_periods(df, short_range, long_range, step)
优化均线周期参数

**参数:**
- `df` (DataFrame): 价格数据
- `short_range` (Tuple): 短期均线范围 (min, max)
- `long_range` (Tuple): 长期均线范围 (min, max)
- `step` (int): 步长

**返回值:** Dict (包含params, result, score)

---

## 注意事项

1. **网络连接**: qstock 需要网络连接获取实时和历史数据
2. **数据延迟**: 免费数据可能有延迟
3. **请求频率**: 避免频繁请求，控制访问频率
4. **风险提示**: 本工具仅供学习研究，不构成投资建议

---

## 常见问题

### Q1: 无法获取数据怎么办？
A: 检查网络连接，或稍后重试。qstock 的数据源可能有访问限制。

### Q2: 选股结果为空？
A: 调整筛选条件，放宽参数范围。

### Q3: 如何提高回测准确性？
A: 使用更长时间的历史数据，优化策略参数。

### Q4: 可以自定义策略吗？
A: 可以，继承 `QStockSelector` 或 `StrategyOptimizer` 类，添加自定义方法。

---

## 更新日志

### v1.0.0 (2026-03-05)
- 初始版本发布
- 实现基础选股功能
- 实现技术指标计算
- 实现策略回测和优化
- 实现多信号共振策略
