"""
简单直接的A股股票列表获取脚本
"""

import sys
from datetime import datetime


def main():
    print("=" * 60)
    print("A股股票列表获取")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 导入qstock
    print("正在导入 qstock...")
    try:
        import qstock as qs
        print("✓ qstock 导入成功")
    except Exception as e:
        print(f"✗ qstock 导入失败: {e}")
        sys.exit(1)

    # 方法1: 尝试 qs.get_data('stock_list')
    print("\n【方法1】使用 qs.get_data('stock_list')...")
    stock_list = None

    try:
        stock_list = qs.get_data('stock_list')

        if stock_list is not None and not stock_list.empty:
            print(f"✓ 成功！获取 {len(stock_list)} 只股票")
            print(f"  列名: {stock_list.columns.tolist()}")

            # 保存
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"all_a_stocks_{timestamp}.csv"
            stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"  已保存到: {filename}")

            # 显示前几行
            print(f"\n前5只股票:")
            print(stock_list.head(5).to_string())

            print("\n成功！")
            return stock_list
        else:
            print("✗ 获取的数据为空")
    except Exception as e:
        print(f"✗ 方法1失败: {e}")
        import traceback
        traceback.print_exc()

    # 方法2: 尝试 get_code
    print("\n【方法2】使用 get_code()...")
    try:
        from qstock.data.trade import get_code
        stock_list = get_code()

        if stock_list is not None and not stock_list.empty:
            print(f"✓ 成功！获取 {len(stock_list)} 只股票")
            print(f"  列名: {stock_list.columns.tolist()}")

            # 保存
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"all_a_stocks_{timestamp}.csv"
            stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"  已保存到: {filename}")

            # 显示前几行
            print(f"\n前5只股票:")
            print(stock_list.head(5).to_string())

            print("\n成功！")
            return stock_list
        else:
            print("✗ 获取的数据为空")
    except Exception as e:
        print(f"✗ 方法2失败: {e}")
        import traceback
        traceback.print_exc()

    # 方法3: 手动调用API
    print("\n【方法3】手动调用东方财富API...")
    try:
        from qstock.data.util import trade_detail_dict, request_header, session
        import pandas as pd
        import time

        url = 'http://push2.eastmoney.com/api/qt/clist/get'
        fs = 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23'
        fields = ",".join(trade_detail_dict.keys())

        df_total = pd.DataFrame()
        page_number = 1
        page_size = 200

        print("  开始获取数据，请稍候...")

        while True:
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

                if page_number % 5 == 0:
                    print(f"  已获取 {page_number} 页，共 {len(df_total)} 只股票")

                page_number += 1

                if page_number > 100:
                    print("  ⚠ 达到最大页数限制")
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

            # 保存
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"all_a_stocks_{timestamp}.csv"
            df_total.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"  已保存到: {filename}")

            # 显示前几行
            print(f"\n前5只股票:")
            display_cols = ['代码', '名称']
            available_cols = [col for col in display_cols if col in df_total.columns]
            if available_cols:
                print(df_total.head(5)[available_cols].to_string())
            else:
                print(df_total.head(5).to_string())

            print("\n成功！")
            return df_total
        else:
            print("✗ 未获取到数据")

    except Exception as e:
        print(f"✗ 方法3失败: {e}")
        import traceback
        traceback.print_exc()

    # 所有方法都失败
    print("\n" + "=" * 60)
    print("失败总结")
    print("=" * 60)
    print("所有方法均无法获取股票列表")
    print("\n可能的原因:")
    print("1. 网络连接问题")
    print("2. 东方财富API限制或变更")
    print("3. qstock库版本问题")
    print("\n建议:")
    print("- 检查网络连接")
    print("- 尝试更新qstock: pip install qstock --upgrade")
    print("- 查看qstock文档: https://github.com/tkfy920/qstock")

    return None


if __name__ == '__main__':
    result = main()

    if result is None or (hasattr(result, 'empty') and result.empty):
        print("\n程序退出，未能获取股票数据")
        sys.exit(1)
