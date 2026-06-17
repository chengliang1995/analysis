"""
诊断 qstock 获取股票列表失败的原因
"""

print("=" * 60)
print("qstock 诊断工具")
print("=" * 60)
print()

# 1. 检查qstock导入
print("【1】检查qstock导入...")
try:
    from qstock.data.trade import market_realtime
    print("✓ market_realtime 导入成功")
except ImportError as e:
    print(f"✗ market_realtime 导入失败: {e}")
    exit(1)

# 2. 检查工具模块
print("\n【2】检查工具模块...")
try:
    from qstock.data.util import trade_detail_dict, request_header, session
    print("✓ 工具模块导入成功")
    print(f"  - trade_detail_dict 字段数: {len(trade_detail_dict)}")
    print(f"  - session 对象: {type(session)}")
except Exception as e:
    print(f"✗ 工具模块导入失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# 3. 测试API连接
print("\n【3】测试API连接...")
try:
    import requests
    url = 'http://push2.eastmoney.com/api/qt/clist/get'
    print(f"  API URL: {url}")

    # 测试简单请求
    test_params = {
        'pn': '1',
        'pz': '10',
        'po': '1',
        'np': '1',
        'fltt': '2',
        'invt': '2',
        'fid': 'f3',
        'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23',
        'fields': ','.join(list(trade_detail_dict.keys())[:10])
    }

    print(f"  发送请求...")
    response = session.get(url, headers=request_header, params=test_params, timeout=30)
    print(f"  状态码: {response.status_code}")

    if response.status_code == 200:
        print("  ✓ API连接成功")
        json_data = response.json()
        print(f"  响应结构: {list(json_data.keys())}")

        if 'data' in json_data:
            print(f"  data字段: {list(json_data['data'].keys())}")
            if 'diff' in json_data['data']:
                diff = json_data['data']['diff']
                print(f"  diff数据条数: {len(diff)}")
                if diff:
                    print(f"  第一条股票代码: {diff[0].get('f12', 'N/A')}")
                else:
                    print("  ⚠ diff数据为空")
            else:
                print("  ⚠ data中没有diff字段")
        else:
            print("  ⚠ 响应中没有data字段")
            print(f"  完整响应: {str(json_data)[:200]}")
    else:
        print(f"  ✗ API请求失败")
        print(f"  响应内容: {response.text[:200]}")

except requests.exceptions.Timeout:
    print("  ✗ API连接超时，请检查网络")
except requests.exceptions.ConnectionError:
    print("  ✗ API连接错误，请检查网络连接")
except Exception as e:
    print(f"  ✗ API测试失败: {e}")
    import traceback
    traceback.print_exc()

# 4. 测试完整参数
print("\n【4】测试完整参数请求...")
try:
    fs = 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23'
    fields = ",".join(trade_detail_dict.keys())

    full_params = (
        ('pn', '1'),
        ('pz', '200'),
        ('po', '1'),
        ('np', '1'),
        ('fltt', '2'),
        ('invt', '2'),
        ('fid', 'f3'),
        ('fs', fs),
        ('fields', fields)
    )

    import time
    time.sleep(1)

    response = session.get(url, headers=request_header, params=full_params, timeout=30)
    print(f"  状态码: {response.status_code}")

    if response.status_code == 200:
        json_response = response.json()

        if 'data' in json_response and 'diff' in json_response['data']:
            diff_data = json_response['data']['diff']
            print(f"  ✓ 获取成功，数据条数: {len(diff_data)}")

            if diff_data:
                import pandas as pd
                df_current = pd.DataFrame(diff_data)
                print(f"  DataFrame形状: {df_current.shape}")
                print(f"  原始列数: {len(df_current.columns)}")

                # 测试重命名
                df_renamed = df_current.rename(columns=trade_detail_dict)
                print(f"  重命名后列数: {len(df_renamed.columns)}")

                # 检查关键列
                print(f"  是否有'f12'(代码): {'f12' in df_current.columns}")
                print(f"  是否有'f14'(名称): {'f14' in df_current.columns}")
                print(f"  重命名后'代码': {'代码' in df_renamed.columns}")
                print(f"  重命名后'名称': {'名称' in df_renamed.columns}")

                if not df_renamed.empty:
                    print("\n  前3条数据:")
                    print(df_renamed.head(3)[['代码', '名称'] if '代码' in df_renamed.columns else df_renamed.columns[:5]].to_string())
            else:
                print("  ✗ diff数据为空")
        else:
            print("  ✗ 数据格式异常")
            print(f"  keys: {list(json_response.keys())}")
            if 'data' in json_response:
                print(f"  data keys: {list(json_response['data'].keys())}")
    else:
        print(f"  ✗ 请求失败")
        print(f"  响应: {response.text[:300]}")

except Exception as e:
    print(f"  ✗ 测试失败: {e}")
    import traceback
    traceback.print_exc()

# 5. 检查市场分类
print("\n【5】检查市场分类参数...")
print("  fs参数说明:")
print("    m:0 t:6  - 深市主板")
print("    m:0 t:80 - 深市创业板")
print("    m:1 t:2  - 沪市主板")
print("    m:1 t:23 - 沪市科创板")
print("  当前使用: m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23")

print("\n" + "=" * 60)
print("诊断完成")
print("=" * 60)
