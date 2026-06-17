"""
使用 AKShare 获取A股股票列表
AKShare 是一个优秀的中国金融数据接口库
"""

import pandas as pd
from datetime import datetime


def main():
    print("=" * 60)
    print("使用 AKShare 获取A股股票列表")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 导入AKShare
    print("正在导入 AKShare...")
    try:
        import akshare as ak
        print("✓ AKShare 导入成功")
    except ImportError:
        print("✗ AKShare 未安装")
        print("  请运行: pip install akshare")
        return

    # 方法1: 获取A股实时行情（推荐）
    print("\n【方法1】获取A股实时行情数据...")
    try:
        print("  正在获取数据，请稍候...")
        stock_list = ak.stock_zh_a_spot_em()

        if not stock_list.empty:
            print(f"✓ 成功获取 {len(stock_list)} 只股票")
            print(f"  列名: {stock_list.columns.tolist()}")

            # 显示前几行
            print(f"\n前10只股票预览:")
            print(stock_list.head(10).to_string())

            # 保存数据
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"all_a_stocks_{timestamp}.csv"
            stock_list.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"\n✓ 数据已保存到: {filename}")

            # 分析数据
            analyze_data(stock_list)

            return stock_list
        else:
            print("✗ 获取的数据为空")
    except Exception as e:
        print(f"✗ 失败: {e}")
        import traceback
        traceback.print_exc()

    # 方法2: 备用方法 - 获取A股基本信息
    print("\n【方法2】获取A股基本信息...")
    try:
        print("  正在获取数据，请稍候...")
        stock_info = ak.stock_zh_a_info_sz_code_name()

        if not stock_info.empty:
            print(f"✓ 成功获取 {len(stock_info)} 只股票")
            print(f"  列名: {stock_info.columns.tolist()}")

            # 显示前几行
            print(f"\n前10只股票预览:")
            print(stock_info.head(10).to_string())

            # 保存数据
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"stock_info_{timestamp}.csv"
            stock_info.to_csv(filename, index=False, encoding='utf-8-sig')
            print(f"\n✓ 数据已保存到: {filename}")

            return stock_info
        else:
            print("✗ 获取的数据为空")
    except Exception as e:
        print(f"✗ 失败: {e}")

    print("\n所有方法均失败")
    return pd.DataFrame()


def analyze_data(stock_list):
    """分析股票数据"""
    print("\n" + "=" * 60)
    print("数据分析")
    print("=" * 60)

    if stock_list.empty:
        print("无数据可分析")
        return

    print(f"股票总数: {len(stock_list)}")
    print(f"列数: {len(stock_list.columns)}")
    print(f"列名: {stock_list.columns.tolist()}")

    # 统计
    print("\n【基本统计】")

    # 价格相关
    price_cols = ['最新价', '现价', 'close', 'price']
    price_col = None
    for col in price_cols:
        if col in stock_list.columns:
            price_col = col
            break

    if price_col:
        print(f"\n{price_col}统计:")
        print(f"  平均: {stock_list[price_col].mean():.2f}")
        print(f"  中位数: {stock_list[price_col].median():.2f}")
        print(f"  最高: {stock_list[price_col].max():.2f}")
        print(f"  最低: {stock_list[price_col].min():.2f}")

        # 价格区间
        price_ranges = [
            (0, 10, "10元以下"),
            (10, 30, "10-30元"),
            (30, 50, "30-50元"),
            (50, 100, "50-100元"),
            (100, float('inf'), "100元以上")
        ]

        print(f"\n价格区间分布:")
        for min_p, max_p, label in price_ranges:
            count = len(stock_list[(stock_list[price_col] >= min_p) & (stock_list[price_col] < max_p)])
            pct = count / len(stock_list) * 100
            print(f"  {label}: {count} 只 ({pct:.1f}%)")

    # 涨跌幅相关
    pct_cols = ['涨跌幅', '涨跌', 'pct_chg', 'change']
    pct_col = None
    for col in pct_cols:
        if col in stock_list.columns:
            pct_col = col
            break

    if pct_col:
        print(f"\n{pct_col}统计:")
        print(f"  平均: {stock_list[pct_col].mean():.2f}%")
        print(f"  上涨: {len(stock_list[stock_list[pct_col] > 0])} 只")
        print(f"  下跌: {len(stock_list[stock_list[pct_col] < 0])} 只")
        print(f"  平盘: {len(stock_list[stock_list[pct_col] == 0])} 只")

        # 涨停
        limit_up = stock_list[stock_list[pct_col] >= 9.8]
        print(f"\n涨停股票: {len(limit_up)} 只")
        if len(limit_up) > 0:
            print("涨停股票列表:")
            for idx, row in limit_up.head(20).iterrows():
                code = row.get('代码', row.get('symbol', ''))
                name = row.get('名称', row.get('name', ''))
                price = row.get(price_col, 0)
                pct = row.get(pct_col, 0)
                print(f"  {name}({code}): {price:.2f}元 ({pct:+.2f}%)")

        # 跌停
        limit_down = stock_list[stock_list[pct_col] <= -9.8]
        print(f"\n跌停股票: {len(limit_down)} 只")

    # 成交额相关
    turnover_cols = ['成交额', 'volume', 'amount']
    turnover_col = None
    for col in turnover_cols:
        if col in stock_list.columns:
            turnover_col = col
            break

    if turnover_col:
        print(f"\n{turnover_col}统计:")
        print(f"  总计: {stock_list[turnover_col].sum()/100000000:.2f} 亿")
        print(f"  平均: {stock_list[turnover_col].mean()/10000:.2f} 万")
        print(f"  最高: {stock_list[turnover_col].max()/10000:.2f} 万")

    # 市值相关
    market_cap_cols = ['总市值', 'market_cap']
    market_cap_col = None
    for col in market_cap_cols:
        if col in stock_list.columns:
            market_cap_col = col
            break

    if market_cap_col:
        print(f"\n{market_cap_col}统计:")
        print(f"  总市值: {stock_list[market_cap_col].sum()/100000000:.2f} 亿")
        print(f"  平均市值: {stock_list[market_cap_col].mean()/10000:.2f} 万")
        print(f"  最大市值: {stock_list[market_cap_col].max()/100000000:.2f} 亿")


if __name__ == '__main__':
    result = main()

    if result is not None and not result.empty:
        print("\n" + "=" * 60)
        print("程序执行完成！")
        print("=" * 60)
    else:
        print("\n程序退出，未能获取股票数据")
