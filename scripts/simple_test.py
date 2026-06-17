"""简单测试"""
print("开始测试...")
import sys
print("Python路径:", sys.executable)

try:
    from qstock.data.util import trade_detail_dict
    print("导入trade_detail_dict成功")
    print("字段数量:", len(trade_detail_dict))
except Exception as e:
    print("导入失败:", e)
    import traceback
    traceback.print_exc()

try:
    import requests
    url = 'http://push2.eastmoney.com/api/qt/clist/get'
    print("\n测试API连接...")
    response = requests.get(url, timeout=10)
    print("响应状态码:", response.status_code)
    print("响应内容:", response.text[:200])
except Exception as e:
    print("API测试失败:", e)
