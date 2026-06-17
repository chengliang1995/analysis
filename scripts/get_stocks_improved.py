"""
改进的股票列表获取脚本
使用多种方法尝试获取A股股票列表
"""

import pandas as pd
from datetime import datetime
import sys


def method1_get_code():
    """方法1: 使用 qstock.data.trade.get_code"""
    print("\n【方法1】尝试使用 get_code()...")
    try:
        from qstock.data.trade import get_code
        stock_list = get_code()

        if not stock_list.empty:
            print(f"✓ 成功获取 {len(stock_list)} 只股票")
            return stock_list
        else:
            print("✗ 获取的数据为空")
            return None
    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method2_qstock_get_data():
    """方法2: 使用 qstock.get_data('stock_list')"""
    print("\n【方法2】尝试使用 qstock.get_data('stock_list')...")
    try:
        import qstock as qs
        stock_list = qs.get_data('stock_list')

        if not stock_list.empty:
            print(f"✓ 成功获取 {len(stock_list)} 只股票")
            return stock_list
        else:
            print("✗ 获取的数据为空")
            return None
    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method3_realtime_data():
    """方法3: 使用 realtime_data 获取部分股票"""
    print("\n【方法3】尝试使用 realtime_data()...")
    try:
        from qstock.data.trade import realtime_data

        # 获取部分热门股票
        test_codes = ['000001', '000002', '600000', '600036', '600519']
        print(f"  测试股票: {test_codes}")

        for code in test_codes:
            try:
                data = realtime_data(code=code)
                if not data.empty:
                    print(f"  ✓ {code} 获取成功: {len(data)} 条数据")
            except Exception as e:
                print(f"  ✗ {code} 获取失败: {e}")

        print("注意: realtime_data 只能获取单只股票，不能获取完整列表")
        return None
    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method4_manual_api():
    """方法4: 手动调用东方财富API"""
    print("\n【方法4】手动调用东方财富API...")
    try:
        from qstock.data.util import trade_detail_dict, request_header, session
        import time

        url = 'http://push2.eastmoney.com/api/qt/clist/get'
        fs = 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23'
        fields = ",".join(trade_detail_dict.keys())

        df_total = pd.DataFrame()
        page_number = 1

        print("  开始获取数据...")

        while True:
            params = (
                ('pn', str(page_number)),
                ('pz', '200'),
                ('po', '1'),
                ('np', '1'),
                ('fltt', '2'),
                ('invt', '2'),
                ('fid', 'f3'),
                ('fs', fs),
                ('fields', fields)
            )

            try:
                time.sleep(0.5)
                response = session.get(url, headers=request_header, params=params, timeout=30)

                if response.status_code != 200:
                    print(f"  ✗ 第{page_number}页请求失败: 状态码 {response.status_code}")
                    break

                json_response = response.json()

                if not json_response.get('data') or not json_response['data'].get('diff'):
                    print(f"  ✓ 第{page_number}页无数据，获取完成")
                    break

                df_current = pd.DataFrame(json_response['data']['diff'])
                df_total = pd.concat([df_total, df_current], ignore_index=True)

                if (page_number - 1) % 5 == 0:
                    print(f"  已获取 {page_number} 页，共 {len(df_total)} 只股票")

                page_number += 1

                # 防止无限循环
                if page_number > 100:
                    print("  ⚠ 达到最大页数限制，停止获取")
                    break

            except Exception as e:
                print(f"  ✗ 第{page_number}页异常: {e}")
                break

        if not df_total.empty:
            # 重命名列
            df_total = df_total.rename(columns=trade_detail_dict)

            # 只选择存在的列
            existing_cols = [col for col in trade_detail_dict.values() if col in df_total.columns]
            df_total = df_total[existing_cols]

            print(f"✓ 成功获取 {len(df_total)} 只股票")
            return df_total
        else:
            print("✗ 未获取到数据")
            return None

    except Exception as e:
        print(f"✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """主函数"""
    print("=" * 60)
    print("A股股票列表获取工具")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    stock_list = None

    # 尝试各种方法
    stock_list = method1_get_code()

    if stock_list is None or stock_list.empty:
        stock_list = method2_qstock_get_data()

    if stock_list is None or stock_list.empty:
        method3_realtime_data()  # 这个方法不会返回完整列表
        stock_list = method4_manual_api()

    # 总结
    print("\n" + "=" * 60)
    print("获取结果总结")
    print("=" * 60)

    if stock_list is not None and not stock_list.empty:
        print(f"✓ 成功获取 {len(stock_list)} 只股票")
        print(f"列名: {stock_list.columns.tolist()}")
        print(f"\n前5只股票:")
        print(stock_list.head(5).to_string())

        # 保存结果
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"stock_list_{timestamp}.csv"
        stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n✓ 数据已保存到: {filename}")

        return stock_list
    else:
        print("✗ 所有方法均失败")
        print("\n建议:")
        print("1. 检查网络连接")
        print("2. 确认 qstock 版本是否正确")
        print("3. 检查防火墙设置")
        print("4. 尝试更换网络环境")
        return pd.DataFrame()


if __name__ == '__main__':
    result = main()

    if result.empty:
        print("\n程序退出，未能获取股票数据")
        sys.exit(1)
    else:
        print("\n程序执行成功！")
