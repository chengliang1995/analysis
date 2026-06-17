"""测试获取股票列表"""
from qstock.data.trade import market_realtime
import pandas as pd

try:
    from qstock.data.util import trade_detail_dict, session, request_header, market_num_dict
    from qstock.data.util import trans_num
    from datetime import datetime
    import time

    # 直接测试获取数据
    fs = 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23'
    fields = ",".join(trade_detail_dict.keys())
    df_total = pd.DataFrame()

    params = (
        ('pn', '1'),
        ('pz', '20'),
        ('po', '1'),
        ('np', '1'),
        ('fltt', '2'),
        ('invt', '2'),
        ('fid', 'f3'),
        ('fs', fs),
        ('fields', fields)
    )

    url = 'http://push2.eastmoney.com/api/qt/clist/get'
    response = session.get(url, headers=request_header, params=params)
    json_response = response.json()

    df_total = pd.DataFrame(json_response['data']['diff'])

    print(f"原始列: {df_total.columns.tolist()}")
    print(f"\ntrade_detail_dict.keys(): {list(trade_detail_dict.keys())[:5]}")
    print(f"trade_detail_dict.values(): {list(trade_detail_dict.values())[:5]}")
    print(f"\n数据:\n{df_total.head()}")

except Exception as e:
    print(f"获取失败: {e}")
    import traceback
    traceback.print_exc()
