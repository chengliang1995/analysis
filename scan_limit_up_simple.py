"""
扫描指定股票列表，查找符合涨停策略的股票。
"""

from qstock_strategy_optimizer import StrategyOptimizer
import pandas as pd
from datetime import datetime
import os

COMMON_STOCKS = [
    '000001', '000002', '000063', '000069', '000858',
    '000895', '000938', '000983', '001979', '002415',
    '002594', '600000', '600036', '600519', '600887',
    '601318', '601398', '601857', '601988', '603259',
]

OUTPUT_DIR = "output"


def scan_stock_list(stock_codes, lookback_days: int = 10, max_workers: int = 4):
    print("=" * 60)
    print("涨停策略扫描器（指定列表）")
    print("=" * 60)
    print(f"扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"扫描数量: {len(stock_codes)} 只股票\n")

    optimizer = StrategyOptimizer()
    stock_list = pd.DataFrame({'code': stock_codes})
    result_df = optimizer.find_limit_up_stocks(
        stock_list,
        lookback_days=lookback_days,
        max_workers=max_workers,
        show_progress=True,
    )

    print("=" * 60)
    if result_df.empty:
        print("未找到符合条件的股票")
        return result_df

    print(f"找到 {len(result_df)} 只符合条件的股票：\n")
    for _, row in result_df.iterrows():
        print(f"【{row['name']}】({row['code']})")
        print(f"  涨停日期: {row['limit_date']}")
        print(f"  涨停涨幅: {row['limit_pct']:.2f}%")
        print(f"  最新收盘: {row['latest_close']:.2f}")
        print(f"  距涨停天数: {row['days_after_limit']} 天\n")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = os.path.join(
        OUTPUT_DIR,
        f"limit_up_stocks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )
    result_df.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"结果已保存到: {filename}")
    return result_df


if __name__ == '__main__':
    scan_stock_list(COMMON_STOCKS)
