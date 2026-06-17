"""
使用备用数据源获取A股股票列表
尝试多个数据源
"""

import pandas as pd
import requests
from datetime import datetime
import time
import json


def method_tushare():
    """方法1: 使用Tushare（需要token）"""
    print("\n【方法1】尝试 Tushare...")
    try:
        import tushare as ts
        print("✓ tushare 已安装")

        # 尝试不需要token的基础功能
        try:
            pro = ts.pro_api()
            # 获取股票列表
            df = pro.stock_basic(exchange='', list_status='L',
                                fields='ts_code,symbol,name,area,industry,list_date')
            print(f"✓ 成功获取 {len(df)} 只股票")
            return df
        except Exception as e:
            print(f"✗ 需要token或API限制: {str(e)[:100]}")
            return None
    except ImportError:
        print("✗ tushare 未安装")
        return None
    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method_akshare():
    """方法2: 使用AKShare"""
    print("\n【方法2】尝试 AKShare...")
    try:
        import akshare as ak
        print("✓ akshare 已安装")

        try:
            # 获取A股股票列表
            df = ak.stock_zh_a_spot_em()
            print(f"✓ 成功获取 {len(df)} 只股票")
            print(f"  列名: {df.columns.tolist()[:10]}")
            return df
        except Exception as e:
            print(f"✗ 获取失败: {str(e)[:100]}")
            return None

    except ImportError:
        print("✗ akshare 未安装")
        print("  安装命令: pip install akshare")
        return None
    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method_yfinance():
    """方法3: 使用yfinance（主要是美股，但可以尝试）"""
    print("\n【方法3】尝试 yfinance...")
    try:
        import yfinance as yf
        print("✓ yfinance 已安装，但不支持A股")
        return None
    except ImportError:
        print("✗ yfinance 未安装（且不支持A股）")
        return None


def method_eastmoney_api():
    """方法4: 尝试东方财富的另一个API"""
    print("\n【方法4】尝试东方财富备用API...")
    try:
        # 尝试不同的API端点
        urls = [
            'http://22.push2.eastmoney.com/api/qt/clist/get',
            'https://push2his.eastmoney.com/api/qt/clist/get',
            'http://80.push2.eastmoney.com/api/qt/clist/get',
        ]

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://quote.eastmoney.com/',
        }

        for url in urls:
            print(f"  尝试: {url}")
            try:
                params = {
                    'pn': '1',
                    'pz': '100',
                    'po': '1',
                    'np': '1',
                    'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
                    'fltt': '2',
                    'invt': '2',
                    'fid': 'f3',
                    'fs': 'm:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23',
                    'fields': 'f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f20,f21,f23'
                }

                response = requests.get(url, headers=headers, params=params, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and data['data'].get('diff'):
                        df = pd.DataFrame(data['data']['diff'])
                        print(f"✓ 成功获取 {len(df)} 只股票")
                        return df
            except Exception as e:
                print(f"  ✗ 失败: {str(e)[:50]}")
                continue

        print("✗ 所有端点均失败")
        return None

    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method_sina_api():
    """方法5: 尝试新浪API"""
    print("\n【方法5】尝试新浪API...")
    try:
        url = 'http://vip.stock.finance.sina.com.cn/corp/go.php/vFD_AllStockField/execute.phtml'

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }

        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            # 新浪返回的是HTML，需要解析
            print("✓ 新浪API可访问，但返回HTML格式")
            print("  需要HTML解析，暂时跳过")
            return None
        else:
            print(f"✗ 状态码: {response.status_code}")
            return None

    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method_baostock():
    """方法6: 使用Baostock"""
    print("\n【方法6】尝试 Baostock...")
    try:
        import baostock as bs
        print("✓ baostock 已安装")

        lg = bs.login()
        if lg.error_code != '0':
            print(f"✗ 登录失败: {lg.error_msg}")
            return None

        rs = bs.query_all_stock(day=datetime.now().strftime('%Y-%m-%d'))
        data_list = []
        while (rs.error_code == '0') & rs.next():
            data_list.append(rs.get_row_data())

        result = pd.DataFrame(data_list, columns=rs.fields)
        bs.logout()

        if not result.empty:
            print(f"✓ 成功获取 {len(result)} 只股票")
            return result
        else:
            print("✗ 未获取到数据")
            return None

    except ImportError:
        print("✗ baostock 未安装")
        print("  安装命令: pip install baostock")
        return None
    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def method_use_qstock_cache():
    """方法7: 尝试从qstock获取历史数据（如果能获取到部分数据）"""
    print("\n【方法7】尝试 qstock web_data（单只股票测试）...")
    try:
        from qstock.data.trade import web_data
        print("✓ web_data 可用")

        # 测试获取一只股票
        test_code = '000001'
        today = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - pd.Timedelta(days=30)).strftime('%Y%m%d')

        try:
            data = web_data(test_code, start=start, end=today, freq='d', fqt=1)
            if not data.empty:
                print(f"✓ 可以获取单只股票数据")
                print(f"  说明: qstock可以获取历史数据，但无法获取股票列表")
                return None
            else:
                print("✗ 无法获取数据")
                return None
        except Exception as e:
            print(f"✗ 失败: {str(e)[:100]}")
            return None

    except Exception as e:
        print(f"✗ 失败: {e}")
        return None


def create_sample_stock_list():
    """方法8: 创建示例股票列表（供测试使用）"""
    print("\n【方法8】创建示例股票列表（测试用）...")
    print("  说明: 这不是真实的完整股票列表，仅用于测试功能")

    # 一些常见的A股代码
    sample_stocks = {
        '代码': [
            '000001', '000002', '000063', '000069', '000858',
            '000895', '000938', '000983', '001979', '002415',
            '002594', '600000', '600036', '600519', '600887',
            '601318', '601398', '601857', '601988', '603259',
            '300001', '300002', '300003', '300004', '300005'
        ],
        '名称': [
            '平安银行', '万科A', '中兴通讯', '华侨城A', '五粮液',
            '张江高科', '紫金矿业', '西山煤电', '招商公路', '海康威视',
            '比亚迪', '浦发银行', '招商银行', '贵州茅台', '伊利股份',
            '中国平安', '工商银行', '中国石油', '中国银行', '药明康德',
            '特锐德', '神州泰岳', '乐普医疗', '南风股份', '探路者'
        ],
        '最新价': [10.0] * 25,
        '涨跌幅': [0.0] * 25
    }

    df = pd.DataFrame(sample_stocks)
    print(f"✓ 创建了 {len(df)} 只示例股票")
    print(f"  仅用于功能测试，非真实数据")

    return df


def main():
    """主函数"""
    print("=" * 60)
    print("A股股票列表获取 - 多数据源尝试")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    stock_list = None

    # 尝试各种方法
    methods = [
        method_akshare,
        method_tushare,
        method_baostock,
        method_eastmoney_api,
        method_sina_api,
        method_use_qstock_cache,
    ]

    for method in methods:
        result = method()
        if result is not None and not result.empty:
            stock_list = result
            break
        time.sleep(1)

    # 如果所有方法都失败，使用示例数据
    if stock_list is None:
        stock_list = create_sample_stock_list()

    # 处理结果
    if stock_list is not None and not stock_list.empty:
        print("\n" + "=" * 60)
        print("获取成功！")
        print("=" * 60)
        print(f"股票数量: {len(stock_list)}")
        print(f"列名: {stock_list.columns.tolist()}")

        # 标准化列名
        if '代码' not in stock_list.columns and 'f12' in stock_list.columns:
            stock_list = stock_list.rename(columns={'f12': '代码'})
        if '名称' not in stock_list.columns and 'f14' in stock_list.columns:
            stock_list = stock_list.rename(columns={'f14': '名称'})
        if 'name' in stock_list.columns:
            stock_list = stock_list.rename(columns={'name': '名称'})
        if 'symbol' in stock_list.columns:
            stock_list = stock_list.rename(columns={'symbol': '代码'})

        # 保存
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"stock_list_{timestamp}.csv"
        stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n✓ 已保存到: {filename}")

        # 显示前几行
        print(f"\n前10只股票:")
        print(stock_list.head(10).to_string())

        print("\n" + "=" * 60)
        print("程序执行完成！")
        print("=" * 60)

        return stock_list
    else:
        print("\n所有方法均失败")
        return pd.DataFrame()


if __name__ == '__main__':
    main()
