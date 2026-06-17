"""
扫描所有A股，查找符合涨停策略的股票
策略：10个交易日内有涨停，且最近一个交易日的收盘价未跌破涨停当天的开盘价格
"""

from qstock_strategy_optimizer import StrategyOptimizer
import pandas as pd
from datetime import datetime
import os


OUTPUT_DIR = "output"

def scan_all_stocks():
    """扫描所有A股，查找符合条件的涨停股票"""

    print("=" * 60)
    print("A股涨停策略扫描器")
    print("=" * 60)
    print(f"扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 创建优化器
    optimizer = StrategyOptimizer()

    # 获取所有A股列表
    print("正在获取A股列表...")
    stock_list = optimizer.get_all_stocks()

    if stock_list.empty:
        print("获取股票列表失败，可尝试:")
        print("  1. python scan_limit_up_simple.py  （使用内置股票列表）")
        print("  2. 检查网络后重试，成功后会缓存到 cache/stock_list.csv")
        return

    print(f"共获取 {len(stock_list)} 只股票")
    print()

    # 扫描所有股票
    print("开始扫描...")
    print("-" * 60)

    result_df = optimizer.find_limit_up_stocks(stock_list, lookback_days=10)

    # 显示结果
    if not result_df.empty:
        print()
        print("=" * 60)
        print(f"找到 {len(result_df)} 只符合条件的股票：")
        print("=" * 60)
        print()

        # 格式化输出
        for idx, row in result_df.iterrows():
            print(f"【{row['name']}】({row['code']})")
            print(f"  涨停日期: {row['limit_date']}")
            print(f"  涨停涨幅: {row['limit_pct']:.2f}%")
            print(f"  涨停开盘: {row['limit_open']:.2f}")
            print(f"  涨停收盘: {row['limit_close']:.2f}")
            print(f"  最新收盘: {row['latest_close']:.2f}")
            print(f"  最新涨跌: {row['latest_pct']:.2f}%")
            print(f"  距涨停天数: {row['days_after_limit']} 天")
            print()

        # 保存结果到CSV
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        filename = os.path.join(
            OUTPUT_DIR,
            f"limit_up_stocks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        result_df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"结果已保存到: {filename}")
    else:
        print()
        print("=" * 60)
        print("未找到符合条件的股票")
        print("=" * 60)

    return result_df

def scan_specific_stocks(codes):
    """扫描指定股票列表"""

    print("=" * 60)
    print("指定股票涨停策略扫描")
    print("=" * 60)
    print()

    # 创建优化器
    optimizer = StrategyOptimizer()

    # 构建股票列表
    stock_list = pd.DataFrame({'code': codes, 'name': [f'股票{c}' for c in codes]})

    print(f"扫描 {len(codes)} 只股票: {', '.join(codes)}")
    print("-" * 60)

    result_df = optimizer.find_limit_up_stocks(stock_list, lookback_days=10)

    # 显示结果
    if not result_df.empty:
        print()
        print(f"找到 {len(result_df)} 只符合条件的股票：")
        print(result_df[['code', 'name', 'limit_date', 'limit_pct', 'latest_close']])
    else:
        print()
        print("未找到符合条件的股票")

    return result_df

if __name__ == '__main__':
    # 选择扫描模式

    # 模式1：扫描所有A股（耗时较长）
    scan_all_stocks()

    # 模式2：扫描指定股票（快速测试）
    # codes = ['000001', '600519', '000858', '600036', '601318']
    # scan_specific_stocks(codes)
