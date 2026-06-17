"""
修复版的A股股票列表获取脚本
绕过qstock的bug，直接使用修复的API调用
"""

import pandas as pd
import requests
from datetime import datetime
import time
import json


def get_stock_list_from_eastmoney():
    """
    直接从东方财富获取股票列表
    绕过qstock的bug
    """
    print("=" * 60)
    print("从东方财富API获取A股列表")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 东方财富API地址
    url = 'http://push2.eastmoney.com/api/qt/clist/get'

    # 请求头 - 模拟真实浏览器
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Referer': 'http://quote.eastmoney.com/',
        'Connection': 'keep-alive'
    }

    # 市场过滤参数
    # m:0 t:6 - 深市主板
    # m:0 t:80 - 深市创业板
    # m:1 t:2 - 沪市主板
    # m:1 t:23 - 沪市科创板
    market_filters = [
        ('m:0 t:6', '深市主板'),
        ('m:0 t:80', '深市创业板'),
        ('m:1 t:2', '沪市主板'),
        ('m:1 t:23', '沪市科创板')
    ]

    # 字段映射
    field_mapping = {
        'f12': '代码',
        'f14': '名称',
        'f2': '最新价',
        'f3': '涨跌幅',
        'f4': '涨跌额',
        'f5': '成交量',
        'f6': '成交额',
        'f7': '振幅',
        'f8': '最高',
        'f9': '最低',
        'f10': '今开',
        'f11': '昨收',
        'f15': '最高价',
        'f16': '最低价',
        'f17': '开盘价',
        'f18': '量比',
        'f20': '总市值',
        'f21': '流通市值',
        'f22': '换手率',
        'f23': '市盈率动态',
        'f24': '市盈率静态',
        'f25': '市净率',
        'f62': '日期',
        'f107': '换手率',
        'f127': '所属行业',
        'f152': '市盈率TTM'
    }

    # 请求字段
    fields = ",".join(field_mapping.keys())

    df_total = pd.DataFrame()
    page_size = 500  # 每页500条

    for market_filter, market_name in market_filters:
        print(f"\n正在获取 {market_name}...")

        page_number = 1

        while True:
            # 请求参数
            params = {
                'pn': str(page_number),
                'pz': str(page_size),
                'po': '1',
                'np': '1',
                'fltt': '2',
                'invt': '2',
                'fid': 'f3',
                'fs': market_filter,
                'fields': fields
            }

            try:
                # 发送请求
                print(f"  第{page_number}页...", end='', flush=True)
                time.sleep(0.3)  # 避免请求过快

                response = requests.get(url, headers=headers, params=params, timeout=30)

                if response.status_code != 200:
                    print(f" ✗ 状态码 {response.status_code}")
                    break

                # 解析JSON
                json_response = response.json()

                # 检查是否有数据
                if not json_response.get('data') or not json_response['data'].get('diff'):
                    print(" 完成")
                    break

                # 转换为DataFrame
                diff_data = json_response['data']['diff']
                df_current = pd.DataFrame(diff_data)

                # 合并数据
                df_total = pd.concat([df_total, df_current], ignore_index=True)

                print(f" ✓ (累计: {len(df_total)} 只)")

                page_number += 1

                # 防止无限循环
                if page_number > 50:
                    print("  ⚠ 达到最大页数限制")
                    break

            except requests.exceptions.Timeout:
                print(" ✗ 超时")
                break
            except requests.exceptions.ConnectionError as e:
                print(f" ✗ 连接错误: {str(e)[:50]}")
                break
            except Exception as e:
                print(f" ✗ 错误: {str(e)[:50]}")
                break

    if not df_total.empty:
        print(f"\n✓ 总共获取 {len(df_total)} 只股票")

        # 重命名列
        df_total = df_total.rename(columns=field_mapping)

        # 只保留有映射的列
        valid_cols = [col for col in field_mapping.values() if col in df_total.columns]
        df_total = df_total[valid_cols]

        # 转换数值类型
        numeric_cols = [col for col in df_total.columns if col not in ['代码', '名称', '日期', '所属行业']]
        for col in numeric_cols:
            df_total[col] = pd.to_numeric(df_total[col], errors='coerce')

        # 格式化日期
        if '日期' in df_total.columns:
            df_total['日期'] = pd.to_datetime(df_total['日期'], unit='s')
            df_total['日期'] = df_total['日期'].dt.strftime('%Y-%m-%d')

        # 去重
        df_total = df_total.drop_duplicates(subset=['代码'])

        # 排序
        df_total = df_total.sort_values('代码').reset_index(drop=True)

        print(f"✓ 去重后剩余 {len(df_total)} 只股票")
        print(f"✓ 包含字段: {df_total.columns.tolist()}")

        # 保存
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"all_a_stocks_{timestamp}.csv"
        df_total.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n✓ 数据已保存到: {filename}")

        # 显示前几行
        print(f"\n前10只股票预览:")
        display_cols = ['代码', '名称', '最新价', '涨跌幅']
        available_cols = [col for col in display_cols if col in df_total.columns]
        print(df_total.head(10)[available_cols].to_string())

        return df_total
    else:
        print("\n✗ 未获取到任何数据")
        return pd.DataFrame()


def analyze_stocks(stock_list):
    """分析股票数据"""
    print("\n" + "=" * 60)
    print("数据分析")
    print("=" * 60)

    if stock_list.empty:
        print("无数据可分析")
        return

    print(f"股票总数: {len(stock_list)}")

    # 价格统计
    if '最新价' in stock_list.columns:
        print(f"\n最新价统计:")
        print(f"  平均: {stock_list['最新价'].mean():.2f} 元")
        print(f"  中位数: {stock_list['最新价'].median():.2f} 元")
        print(f"  最高: {stock_list['最新价'].max():.2f} 元")
        print(f"  最低: {stock_list['最新价'].min():.2f} 元")

    # 涨跌幅统计
    if '涨跌幅' in stock_list.columns:
        print(f"\n涨跌幅统计:")
        print(f"  平均: {stock_list['涨跌幅'].mean():.2f}%")
        print(f"  上涨: {len(stock_list[stock_list['涨跌幅'] > 0])} 只")
        print(f"  下跌: {len(stock_list[stock_list['涨跌幅'] < 0])} 只")

        # 涨停股票
        limit_up = stock_list[stock_list['涨跌幅'] >= 9.8]
        print(f"  涨停: {len(limit_up)} 只")
        if not limit_up.empty:
            print(f"\n涨停股票列表:")
            for _, stock in limit_up.head(20).iterrows():
                price = stock.get('最新价', 0)
                pct = stock.get('涨跌幅', 0)
                print(f"    {stock.get('名称', '')}({stock.get('代码', '')}): {price:.2f}元 (+{pct:.2f}%)")

    # 跌停股票
    if '涨跌幅' in stock_list.columns:
        limit_down = stock_list[stock_list['涨跌幅'] <= -9.8]
        print(f"  跌停: {len(limit_down)} 只")


def main():
    """主函数"""
    print("\n")

    # 获取股票列表
    stock_list = get_stock_list_from_eastmoney()

    if stock_list.empty:
        print("\n程序退出，未能获取股票数据")
        return

    # 分析数据
    analyze_stocks(stock_list)

    print("\n" + "=" * 60)
    print("程序执行完成！")
    print("=" * 60)


if __name__ == '__main__':
    main()
