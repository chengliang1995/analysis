"""测试 qstock 数据获取"""
from qstock.data.util import trade_detail_dict, request_header, session
import requests
import json
import time

# 测试东方财富API
fs = 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23'
fields = ",".join(trade_detail_dict.keys())
page_size = 200
page_number = 1

params = (
    ('pn', str(page_number)),
    ('pz', str(page_size)),
    ('po', '1'),
    ('np', '1'),
    ('fltt', '2'),
    ('invt', '2'),
    ('fid', 'f3'),
    ('fs', fs),
    ('fields', fields)
)

url = 'http://push2.eastmoney.com/api/qt/clist/get'

print("请求URL:", url)
print("请求参数:", params)
print()

try:
    time.sleep(1)
    response = session.get(url, headers=request_header, params=params)
    print("响应状态码:", response.status_code)
    
    if response.status_code == 200:
        json_response = response.json()
        print("响应数据结构:", list(json_response.keys()))
        
        if 'data' in json_response:
            print("data字段:", list(json_response['data'].keys()))
            
            if 'diff' in json_response['data']:
                diff_data = json_response['data']['diff']
                print("diff数据条数:", len(diff_data))
                
                if diff_data:
                    print("第一条数据:", diff_data[0])
                    
                    # 转换为DataFrame
                    import pandas as pd
                    df_current = pd.DataFrame(diff_data)
                    print(f"\nDataFrame形状: {df_current.shape}")
                    print(f"列名: {df_current.columns.tolist()[:10]}")
                    
                    # 重命名
                    df_total = df_current.rename(columns=trade_detail_dict)
                    print(f"\n重命名后列名: {df_total.columns.tolist()[:10]}")
                else:
                    print("diff数据为空")
            else:
                print("没有diff字段")
        else:
            print("没有data字段")
            print("完整响应:", json_response)
    else:
        print("请求失败，响应内容:", response.text[:500])
        
except Exception as e:
    print(f"请求异常: {e}")
    import traceback
    traceback.print_exc()
