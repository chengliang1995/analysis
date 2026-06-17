# A股股票数据获取指南

## 问题分析

之前尝试使用 qstock 获取A股数据失败的原因：

1. **网络连接被拒绝** - 东方财富API检测到自动化请求并主动断开连接
2. **qstock内部bug** - `market_realtime()` 和 `get_code()` 函数期望的列名与API返回的数据不匹配
3. **API反爬虫机制** - 需要完整的浏览器User-Agent和合理的请求频率

## 解决方案

### 推荐方法：使用 AKShare

AKShare 是一个优秀的开源中国金融数据接口库，维护活跃，数据可靠。

#### 安装 AKShare

```bash
pip install akshare
```

#### 使用方法

```python
import akshare as ak

# 获取A股实时行情数据（推荐）
stock_list = ak.stock_zh_a_spot_em()
print(f"获取到 {len(stock_list)} 只股票")
```

#### 完整示例

参考 `get_stocks_akshare.py` 文件，包含：
- 获取实时行情数据
- 数据统计分析
- 自动保存为CSV

### 备用方法

#### 方法1: 使用 Baostock

```bash
pip install baostock
```

```python
import baostock as bs
import pandas as pd

lg = bs.login()
rs = bs.query_all_stock(day='2024-01-01')
data_list = []
while (rs.error_code == '0') & rs.next():
    data_list.append(rs.get_row_data())
result = pd.DataFrame(data_list, columns=rs.fields)
bs.logout()
```

#### 方法2: 使用 Tushare Pro

需要申请 token，功能强大但需要注册账号。

## 可用的脚本

### 1. get_stocks_akshare.py（推荐）

最简单可靠的方法，使用 AKShare 获取数据。

```bash
python get_stocks_akshare.py
```

功能：
- 获取所有A股实时行情
- 详细的数据分析
- 自动保存为CSV

### 2. get_all_a_stocks.py

集成了多种方法的获取脚本，优先使用 AKShare。

```bash
python get_all_a_stocks.py
```

功能：
- 自动选择最佳数据源
- 获取股票列表
- 可选获取历史数据

### 3. qstock_strategy_optimizer.py

已更新，添加了多种后备方案。

## 数据说明

### AKShare 返回的字段

`stock_zh_a_spot_em()` 返回的字段包括：

- `代码`: 股票代码
- `名称`: 股票名称
- `最新价`: 最新价格
- `涨跌幅`: 涨跌幅百分比
- `涨跌额`: 涨跌额
- `成交量`: 成交量
- `成交额`: 成交额
- `振幅`: 振幅
- `最高`: 最高价
- `最低`: 最低价
- `今开`: 今日开盘价
- `昨收`: 昨日收盘价
- `量比`: 量比
- `换手率`: 换手率
- `市盈率-动态`: 动态市盈率
- `市净率`: 市净率
- `总市值`: 总市值
- `流通市值`: 流通市值
- 等等...

## 快速开始

### 1. 仅获取股票列表

```bash
python get_stocks_akshare.py
```

### 2. 获取股票列表和历史数据

```bash
python get_all_a_stocks.py
# 然后选择选项1、2或3获取历史数据
```

### 3. 使用涨停策略扫描

```bash
python scan_limit_up_simple.py
```

## 注意事项

1. **网络要求**: 需要稳定的网络连接
2. **数据更新**: 实时行情数据每个交易日更新
3. **请求频率**: 避免过于频繁的请求，可能被限制
4. **数据验证**: 使用前请验证数据的准确性
5. **合规使用**: 仅用于个人学习和研究，遵守相关法规

## 常见问题

### Q: AKShare 安装失败？

A: 使用国内镜像源：
```bash
pip install akshare -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: 获取数据时超时？

A:
1. 检查网络连接
2. 尝试更换网络环境
3. 使用备用数据源

### Q: 数据为空？

A:
1. 检查是否在交易时间
2. 某些数据源在非交易时间可能不更新
3. 尝试其他数据源

## 下一步建议

1. **数据预处理**: 清洗和标准化数据
2. **数据分析**: 使用 pandas 和 numpy 进行分析
3. **策略回测**: 基于历史数据验证策略
4. **可视化**: 使用 matplotlib 或 plotly 绘制图表

## 参考资源

- AKShare 官方文档: https://akshare.akfamily.xyz/
- Baostock 官方文档: http://baostock.com/
- Tushare Pro: https://tushare.pro/

## 更新日志

- 2026-03-25: 创建AKShare获取方案，解决qstock网络连接问题
