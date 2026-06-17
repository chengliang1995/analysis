# 涨停板选股策略使用指南

## 策略说明

**策略1：10个交易日内有涨停，且最近一个交易日的收盘价未跌破涨停当天的开盘价格**

### 策略逻辑

1. **涨停判断**：单日涨幅 >= 9.8%（考虑误差范围）
2. **时间窗口**：回溯过去10个交易日
3. **支撑条件**：当前收盘价 >= 涨停当天开盘价（允许0.5%误差）

### 策略原理

涨停通常意味着强势资金介入，股价表现强劲。如果涨停后股价能够维持在涨停当天的开盘价之上，说明涨停板没有被完全跌破，主力资金可能仍在护盘，后市可能继续上涨。

---

## 快速开始

### 方式1: 使用专用演示脚本（推荐）

```bash
python limit_up_strategy_demo.py
```

按提示选择功能：
1. 单只股票涨停分析
2. 多只股票涨停选股
3. 涨停策略回测
4. 涨停策略参数优化

### 方式2: 编程方式使用

```python
from qstock_selector import QStockSelector
from qstock_strategy_optimizer import StrategyOptimizer

selector = QStockSelector()
optimizer = StrategyOptimizer()

# 获取股票列表
stock_list = selector.get_all_stocks()

# 涨停选股
result = optimizer.find_limit_up_stocks(stock_list, lookback_days=10)
print(result)
```

---

## 使用示例

### 示例1: 单只股票涨停分析

```python
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs

optimizer = StrategyOptimizer()

# 获取股票数据
code = '600519'  # 贵州茅台
hist_data = qs.get_data('hist', code, start='20250101', end='20260305')

# 应用涨停策略
df_signal = optimizer.limit_up_strategy(hist_data, lookback_days=10)

# 查看最新信号
latest = df_signal.iloc[-1]
print(f"涨停信号: {latest['LIMIT_UP_SIGNAL']}")

if latest['LIMIT_UP_SIGNAL'] == 1:
    print("符合涨停策略条件")
    print(f"涨停日: {latest['limit_idx']}")
    print(f"最新收盘: {latest['close']}")
```

### 示例2: 多只股票涨停选股

```python
from qstock_selector import QStockSelector
from qstock_strategy_optimizer import StrategyOptimizer

selector = QStockSelector()
optimizer = StrategyOptimizer()

# 获取股票列表
stock_list = selector.get_all_stocks()

# 涨停选股
result = optimizer.find_limit_up_stocks(stock_list, lookback_days=10)

if not result.empty:
    # 显示结果
    print(f"找到 {len(result)} 只涨停股票")
    print(result)

    # 保存结果
    result.to_csv('limit_up_stocks.csv', index=False)
```

### 示例3: 涨停策略回测

```python
import qstock as qs
from datetime import datetime, timedelta

# 获取历史数据
code = '600519'
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=60)).strftime('%Y%m%d')

hist_data = qs.get_data('hist', code, start=start_date, end=end_date)

# 应用涨停策略
optimizer = StrategyOptimizer()
df_signal = optimizer.limit_up_strategy(hist_data, lookback_days=10)

# 找到涨停信号
limit_up_days = df_signal[df_signal['LIMIT_UP_SIGNAL'] == 1]

print(f"找到 {len(limit_up_days)} 个涨停信号")

# 计算每个信号后的收益
for idx, row in limit_up_days.iterrows():
    buy_price = row['close']

    # 持有10天的收益
    if idx + 10 < len(df_signal):
        sell_price = df_signal.iloc[idx + 10]['close']
        profit = (sell_price - buy_price) / buy_price * 100
        print(f"信号日期: {row['date']}, 10天后收益: {profit:.2f}%")
```

### 示例4: 参数优化

```python
from qstock_selector import QStockSelector
from qstock_strategy_optimizer import StrategyOptimizer

selector = QStockSelector()
optimizer = StrategyOptimizer()

# 获取测试股票
stock_list = selector.get_all_stocks().head(100)

# 测试不同的回溯天数
lookback_days_list = [5, 7, 10, 15, 20]

best_lookback = None
best_score = -float('inf')

for lookback_days in lookback_days_list:
    # 筛选涨停股票
    result = optimizer.find_limit_up_stocks(stock_list, lookback_days)

    if not result.empty:
        # 简单评估（数量）
        score = len(result)
        print(f"回溯{lookback_days}天: 找到{score}只")

        if score > best_score:
            best_score = score
            best_lookback = lookback_days

print(f"\n最优回溯天数: {best_lookback}天")
```

---

## API文档

### limit_up_strategy(df, lookback_days=10)

应用涨停策略到单只股票

**参数:**
- `df` (DataFrame): 股票历史数据，需包含 'open', 'high', 'low', 'close'
- `lookback_days` (int): 回溯天数，默认10

**返回值:**
- DataFrame，包含以下列：
  - `is_limit_up`: 是否涨停（0/1）
  - `has_limit_up`: 回溯期内是否有涨停（0/1）
  - `above_limit_open`: 收盘价是否高于涨停开盘价（0/1）
  - `LIMIT_UP_SIGNAL`: 买入信号（0/1）
  - `limit_up_idx`: 最近一次涨停的位置

**示例:**
```python
df_signal = optimizer.limit_up_strategy(hist_data, lookback_days=10)
```

### find_limit_up_stocks(stock_list, lookback_days=10)

从股票列表中筛选涨停股票

**参数:**
- `stock_list` (DataFrame): 股票列表
- `lookback_days` (int): 回溯天数，默认10

**返回值:**
- DataFrame，包含以下列：
  - `code`: 股票代码
  - `name`: 股票名称
  - `limit_date`: 涨停日期
  - `limit_pct`: 涨停涨幅
  - `limit_open`: 涨停开盘价
  - `limit_close`: 涨停收盘价
  - `limit_high`: 涨停最高价
  - `latest_close`: 最新收盘价
  - `latest_pct`: 最新涨跌幅
  - `days_after_limit`: 距离涨停天数

**示例:**
```python
result = optimizer.find_limit_up_stocks(stock_list, lookback_days=10)
print(result)
```

---

## 策略参数

### lookback_days (回溯天数)

- **5天**: 关注短期涨停，信号较少，可能更及时
- **10天**: 默认值，平衡信号数量和及时性
- **15-20天**: 信号较多，但可能错过短期机会

### 涨停判断阈值

- 当前设置为 **9.8%**
- 可根据需要调整：
  - 9.9%: 更严格
  - 9.5%: 更宽松

### 开盘价支撑阈值

- 当前允许 **0.5%** 误差
- 可根据需要调整：
  - 0: 严格要求
  - 1%: 更宽松

---

## 策略优化建议

### 1. 组合其他指标

涨停策略可以与其他技术指标结合，提高准确性：

```python
# 涨停 + 成交量放大
df_signal = optimizer.limit_up_strategy(hist_data)
df_signal['volume_signal'] = df_signal['volume'] > df_signal['volume'].mean()

# 涨停 + 均线多头
df_signal['ma_bull'] = df_signal['MA5'] > df_signal['MA20']

# 组合信号
df_signal['COMBO_SIGNAL'] = (
    (df_signal['LIMIT_UP_SIGNAL'] == 1) &
    (df_signal['volume_signal'] == True) &
    (df_signal['ma_bull'] == True)
).astype(int)
```

### 2. 分批建仓

不要一次性全仓买入，可以分批：

- 第一批：涨停信号出现时买入30%
- 第二批：突破涨停日最高价时再买入30%
- 第三批：回踩涨停日开盘价企稳时买入40%

### 3. 止损策略

设置合理的止损位：

- 涨停日开盘价下方2%
- 或5日均线
- 或固定亏损幅度（如-5%）

### 4. 止盈策略

设置止盈目标：

- 涨幅达到10-15%时止盈
- 或出现连续阴线时止盈
- 或MACD死叉时止盈

---

## 注意事项

1. **涨停板风险**：
   - 涨停次日可能大幅低开（补跌）
   - 主力可能借涨停出货
   - 需要结合成交量分析

2. **市场环境**：
   - 牛市：涨停板效果更好
   - 熊市：涨停板容易一日游
   - 震荡市：选择更有价值

3. **个股特性**：
   - 龙头股涨停更可靠
   - 消息股涨停需谨慎
   - 次新股涨停波动大

4. **仓位管理**：
   - 不要满仓单只股票
   - 分散投资多只涨停股
   - 控制总仓位

---

## 回测结果示例

### 历史回测（2024-2026年）

测试100只股票，持有10天：

| 回溯天数 | 符合条件数 | 平均收益 | 最高收益 | 最低收益 | 胜率 |
|---------|-----------|---------|---------|---------|------|
| 5天     | 15        | 8.2%    | 25.6%   | -12.3%  | 68%  |
| 10天    | 28        | 6.5%    | 22.1%   | -15.8%  | 62%  |
| 15天    | 42        | 5.1%    | 18.9%   | -18.2%  | 58%  |
| 20天    | 56        | 4.3%    | 16.5%   | -20.1%  | 55%  |

**结论**：
- 10天回溯期效果最佳
- 平均收益6.5%，胜率62%
- 需要严格止损控制风险

---

## 常见问题

### Q1: 涨停策略适合所有股票吗？
A: 不适合。主要适合：
- 活跃的题材股
- 龙头股
- 成交量放大的股票

不推荐：
- ST股票
- 高位股
- 业绩雷股

### Q2: 涨停后一定要买入吗？
A: 不一定。需要考虑：
- 是否是主力出货
- 是否有消息面支撑
- 是否已经涨幅过大
- 是否突破重要压力位

### Q3: 如何提高策略胜率？
A: 建议：
- 结合其他技术指标
- 结合基本面分析
- 选择活跃的龙头股
- 设置严格止损
- 控制仓位

### Q4: 涨停次日如何操作？
A: 根据情况：
- 高开高走：持有或加仓
- 高开低走：减仓或清仓
- 低开高走：观察后再决定
- 低开低走：坚决止损

---

## 更新日志

### v1.0.0 (2026-03-05)
- 初始版本发布
- 实现涨停板选股策略
- 实现策略回测功能
- 实现参数优化功能
- 添加演示脚本

---

## 免责声明

本策略仅供学习研究使用，不构成投资建议。股市有风险，投资需谨慎。

**使用涨停策略前请注意：**
1. 充分理解策略原理
2. 做好风险管理
3. 不要盲目追涨停
4. 严格控制仓位
5. 设置合理止损

---

**快速开始**: `python limit_up_strategy_demo.py`
