# A股数据获取问题 - 网络连接解决方案

## 当前问题

所有尝试的在线API（qstock、AKShare、东方财富等）都返回连接被拒绝错误：

```
ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

这说明：
1. **严格反爬虫** - 服务器检测到自动化请求并主动断开
2. **网络限制** - 可能ISP或防火墙限制了对金融数据API的访问
3. **IP被封** - 频繁请求导致IP被临时封禁

## 解决方案

### 方案1: 使用离线股票代码列表（推荐）⭐

运行离线模式脚本：

```bash
python get_stocks_offline.py
```

这个脚本：
- 内置了200+只常见A股代码（涵盖各行业龙头）
- 支持手动添加自定义股票代码
- 保存到本地文件，无需联网

### 方案2: 使用VPN/代理

如果有可用的代理，可以设置：

```python
import requests

proxies = {
    'http': 'http://your-proxy:port',
    'https': 'https://your-proxy:port',
}

response = requests.get(url, proxies=proxies)
```

### 方案3: 使用专业数据源

注册使用付费数据服务：

1. **Tushare Pro** - https://tushare.pro/
   - 需要申请token
   - 功能强大，数据质量高

2. **聚宽** - https://www.joinquant.com/
   - 量化交易平台
   - 提供完整的历史数据

3. **米筐** - https://www.ricequant.com/
   - 量化研究平台
   - 丰富的数据接口

4. **Wind/Choice** - 商业数据终端
   - 最全面的金融数据
   - 需要订阅

## 快速开始

### 步骤1: 使用离线股票列表

```bash
python get_stocks_offline.py
```

按照提示操作即可。

### 步骤2: 获取单只股票历史数据

即使无法获取股票列表，qstock仍然可以获取单个股票的历史数据：

```python
from qstock.data.trade import web_data
from datetime import datetime

# 获取贵州茅台的历史数据
code = '600519'
start = '20240101'
end = datetime.now().strftime('%Y%m%d')

data = web_data(code, start=start, end=end, freq='d', fqt=1)
print(data.head())
```

### 步骤3: 使用涨停策略扫描

```bash
python scan_limit_up_simple.py
```

该脚本使用内置的股票列表，无需联网获取股票代码。

## 总结

**当前最佳方案**：使用 `get_stocks_offline.py` 的离线模式
- 立即可用，无需联网
- 包含200+只主要A股
- 可手动添加股票
- 配合qstock获取历史数据

**长期方案**：注册专业数据服务
- Tushare Pro（推荐新手）
- 聚宽/米筐（量化平台）
- Wind/Choice（专业机构）
