#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
涨停板选股策略演示
策略：10个交易日内有涨停，且最近一个交易日的收盘价未跌破涨停当天的开盘价格
"""

from qstock_selector import QStockSelector
from qstock_strategy_optimizer import StrategyOptimizer
import qstock as qs
import pandas as pd
from datetime import datetime, timedelta
import os


def single_stock_analysis():
    """单只股票涨停分析"""
    print("\n" + "="*70)
    print("单只股票涨停分析")
    print("="*70)

    optimizer = StrategyOptimizer()

    # 分析指定股票
    code = input("请输入股票代码（如 600519，留空则使用默认）：").strip() or '600519'

    print(f"\n正在获取 {code} 的历史数据...")

    try:
        # 获取历史数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

        hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

        if hist_data.empty:
            print("获取数据失败")
            return

        print(f"成功获取 {len(hist_data)} 条数据")

        # 应用涨停策略
        df_signal = optimizer.limit_up_strategy(hist_data, lookback_days=10)

        # 计算涨跌幅
        df_signal['pct_chg'] = ((df_signal['close'] - df_signal['close'].shift(1)) /
                                df_signal['close'].shift(1) * 100)

        # 显示最近数据
        print("\n" + "="*70)
        print(f"{code} 近期数据")
        print("="*70)

        display_cols = ['date', 'open', 'high', 'low', 'close', 'pct_chg',
                       'is_limit_up', 'has_limit_up', 'above_limit_open', 'LIMIT_UP_SIGNAL']

        if all(col in df_signal.columns for col in display_cols):
            recent_data = df_signal[display_cols].tail(15)
            print(recent_data.to_string(index=False))

        # 检查最新信号
        latest = df_signal.iloc[-1]
        print("\n" + "="*70)
        print(f"{code} 最新信号")
        print("="*70)

        if latest['LIMIT_UP_SIGNAL'] == 1:
            print("✓ 符合涨停策略条件！")

            # 显示涨停信息
            limit_idx = latest['limit_up_idx']
            if limit_idx < len(df_signal):
                limit_day = df_signal.iloc[limit_idx]
                print(f"\n涨停信息:")
                print(f"  涨停日期: {limit_day.get('date', 'N/A')}")
                print(f"  涨停涨幅: {limit_day.get('pct_chg', 0):.2f}%")
                print(f"  涨停开盘: {limit_day['open']:.2f}")
                print(f"  涨停收盘: {limit_day['close']:.2f}")
                print(f"  涨停最高: {limit_day['high']:.2f}")

                print(f"\n最新信息:")
                print(f"  最新收盘: {latest['close']:.2f}")
                print(f"  最新涨跌: {latest.get('pct_chg', 0):.2f}%")
                print(f"  距涨停天数: {len(df_signal) - limit_idx - 1} 天")

                # 检查是否跌破涨停开盘价
                if latest['above_limit_open'] == 1:
                    print(f"  收盘价未跌破涨停开盘价 ✓")
                else:
                    print(f"  收盘价已跌破涨停开盘价 ✗")

        else:
            print("✗ 不符合涨停策略条件")

            # 检查是否有涨停
            if latest['has_limit_up'] == 0:
                print("  近10个交易日无涨停")
            else:
                print("  虽有涨停，但收盘价已跌破涨停开盘价")

        # 保存数据
        os.makedirs('output', exist_ok=True)
        filename = 'output/single_stock_limit_up.csv'
        df_signal.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n数据已保存到: {filename}")

    except Exception as e:
        print(f"分析失败: {e}")
        import traceback
        traceback.print_exc()


def multi_stock_selection():
    """多只股票涨停选股"""
    print("\n" + "="*70)
    print("涨停板选股")
    print("="*70)

    optimizer = StrategyOptimizer()

    # 获取股票列表
    print("正在获取股票列表...")
    selector = QStockSelector()
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("获取股票列表失败")
        return

    print(f"成功获取 {len(stock_list)} 只股票")

    # 限制数量（演示用）
    max_stocks = input("请输入要分析的股票数量（默认100，回车使用默认）：").strip()

    if max_stocks:
        try:
            max_stocks = int(max_stocks)
        except:
            max_stocks = 100
    else:
        max_stocks = 100

    demo_stocks = stock_list.head(max_stocks)
    print(f"\n将分析前 {max_stocks} 只股票...")

    # 涨停选股
    print("正在分析涨停股票...")
    result = optimizer.find_limit_up_stocks(demo_stocks, lookback_days=10)

    if result.empty:
        print("\n没有找到符合条件的股票")
        return

    print("\n" + "="*70)
    print("涨停选股结果")
    print("="*70)
    print(f"找到 {len(result)} 只符合条件的股票\n")

    print(result.to_string(index=False))

    # 保存结果
    os.makedirs('output', exist_ok=True)
    filename = 'output/limit_up_selection.csv'
    result.to_csv(filename, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存到: {filename}")

    # 按涨停涨幅排序
    print("\n" + "="*70)
    print("按涨停涨幅排序 TOP 10")
    print("="*70)
    top_by_pct = result.sort_values('limit_pct', ascending=False).head(10)
    print(top_by_pct.to_string(index=False))


def limit_up_backtest():
    """涨停策略回测"""
    print("\n" + "="*70)
    print("涨停策略回测")
    print("="*70)

    optimizer = StrategyOptimizer()

    # 获取股票列表
    print("正在获取股票列表...")
    selector = QStockSelector()
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("获取股票列表失败")
        return

    # 限制数量
    max_stocks = input("请输入回测股票数量（默认50，回车使用默认）：").strip()
    if max_stocks:
        try:
            max_stocks = int(max_stocks)
        except:
            max_stocks = 50
    else:
        max_stocks = 50

    demo_stocks = stock_list.head(max_stocks)
    print(f"将回测前 {max_stocks} 只股票...")

    # 找出涨停股票
    print("正在筛选涨停股票...")
    limit_up_stocks = optimizer.find_limit_up_stocks(demo_stocks, lookback_days=10)

    if limit_up_stocks.empty:
        print("\n没有找到涨停股票，无法回测")
        return

    print(f"\n找到 {len(limit_up_stocks)} 只涨停股票")

    # 简单回测：买入后持有N天的收益
    hold_days = input("请输入持有天数（默认20，回车使用默认）：").strip()
    if hold_days:
        try:
            hold_days = int(hold_days)
        except:
            hold_days = 20
    else:
        hold_days = 20

    print(f"\n开始回测（持有 {hold_days} 天）...")

    backtest_results = []

    for idx, stock in limit_up_stocks.iterrows():
        code = stock['code']

        try:
            # 获取历史数据
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=hold_days + 30)).strftime('%Y%m%d')

            hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

            if hist_data.empty or len(hist_data) < hold_days:
                continue

            # 找到涨停日
            limit_date = stock['limit_date']
            limit_row = hist_data[hist_data['date'] == limit_date]

            if limit_row.empty:
                continue

            # 涨停日收盘价作为买入价
            buy_price = limit_row.iloc[0]['close']
            limit_idx = limit_row.index[0]

            # 卖出价格（持有N天后）
            if limit_idx + hold_days < len(hist_data):
                sell_price = hist_data.iloc[limit_idx + hold_days]['close']
                actual_hold_days = hold_days
            else:
                # 如果数据不足，用最后一个收盘价
                sell_price = hist_data.iloc[-1]['close']
                actual_hold_days = len(hist_data) - limit_idx - 1

            # 计算收益
            profit_pct = (sell_price - buy_price) / buy_price * 100

            backtest_results.append({
                'code': code,
                'name': stock['name'],
                'limit_date': limit_date,
                'buy_price': buy_price,
                'sell_price': sell_price,
                'profit_pct': profit_pct,
                'hold_days': actual_hold_days
            })

        except Exception as e:
            continue

    if backtest_results:
        backtest_df = pd.DataFrame(backtest_results)

        print("\n" + "="*70)
        print(f"涨停策略回测结果（持有{hold_days}天）")
        print("="*70)

        print(f"  总数: {len(backtest_df)} 只")
        print(f"  平均收益: {backtest_df['profit_pct'].mean():.2f}%")
        print(f"  最高收益: {backtest_df['profit_pct'].max():.2f}%")
        print(f"  最低收益: {backtest_df['profit_pct'].min():.2f}%")
        print(f"  胜率: {(backtest_df['profit_pct'] > 0).sum() / len(backtest_df) * 100:.1f}%")

        print("\n" + "="*70)
        print("收益排名 TOP 10")
        print("="*70)
        top10 = backtest_df.sort_values('profit_pct', ascending=False).head(10)
        print(top10.to_string(index=False))

        # 保存结果
        os.makedirs('output', exist_ok=True)
        filename = 'output/limit_up_backtest.csv'
        backtest_df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n回测结果已保存到: {filename}")

    else:
        print("回测失败，无有效数据")


def strategy_optimization():
    """涨停策略参数优化"""
    print("\n" + "="*70)
    print("涨停策略参数优化")
    print("="*70)

    optimizer = StrategyOptimizer()

    # 获取测试股票
    print("正在获取测试股票...")
    selector = QStockSelector()
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("获取股票列表失败")
        return

    test_stocks = stock_list.head(50)
    print(f"使用 {len(test_stocks)} 只股票进行优化\n")

    # 测试不同的回溯天数
    lookback_days_list = [5, 7, 10, 15, 20]

    print("开始优化回溯天数参数...")

    best_result = None
    best_lookback = None
    best_score = -float('inf')

    for lookback_days in lookback_days_list:
        try:
            print(f"\n--- 测试回溯天数: {lookback_days} ---")

            # 筛选涨停股票
            limit_up_stocks = optimizer.find_limit_up_stocks(test_stocks, lookback_days)

            if limit_up_stocks.empty:
                print("  无涨停股票")
                continue

            # 简单回测
            backtest_results = []

            for idx, stock in limit_up_stocks.iterrows():
                code = stock['code']

                try:
                    end_date = datetime.now().strftime('%Y%m%d')
                    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')

                    hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

                    if hist_data.empty:
                        continue

                    limit_date = stock['limit_date']
                    limit_row = hist_data[hist_data['date'] == limit_date]

                    if limit_row.empty:
                        continue

                    buy_price = limit_row.iloc[0]['close']
                    limit_idx = limit_row.index[0]

                    # 持有10天
                    if limit_idx + 10 < len(hist_data):
                        sell_price = hist_data.iloc[limit_idx + 10]['close']
                    else:
                        sell_price = hist_data.iloc[-1]['close']

                    profit_pct = (sell_price - buy_price) / buy_price * 100

                    backtest_results.append({
                        'code': code,
                        'profit_pct': profit_pct
                    })

                except:
                    continue

            if backtest_results:
                backtest_df = pd.DataFrame(backtest_results)
                score = backtest_df['profit_pct'].mean()
                win_rate = (backtest_df['profit_pct'] > 0).sum() / len(backtest_df) * 100

                print(f"  符合条件: {len(limit_up_stocks)} 只")
                print(f"  可回测: {len(backtest_df)} 只")
                print(f"  平均收益: {score:.2f}%")
                print(f"  胜率: {win_rate:.1f}%")

                # 评估（平均收益）
                if score > best_score:
                    best_score = score
                    best_lookback = lookback_days
                    best_result = backtest_df
                    print(f"  ✓ 发现更优参数!")

        except Exception as e:
            print(f"  测试失败: {e}")
            continue

    if best_lookback is not None:
        print("\n" + "="*70)
        print("优化结果")
        print("="*70)
        print(f"最优回溯天数: {best_lookback} 天")
        print(f"最优平均收益: {best_score:.2f}%")

        # 保存结果
        os.makedirs('output', exist_ok=True)
        filename = 'output/limit_up_optimization.csv'
        best_result.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n结果已保存到: {filename}")
    else:
        print("优化失败，未找到有效参数")


def main():
    """主函数"""
    print("="*70)
    print("涨停板选股策略演示")
    print("="*70)
    print()
    print("策略说明：10个交易日内有涨停，")
    print("且最近一个交易日的收盘价未跌破涨停当天的开盘价格")
    print()

    print("请选择功能:")
    print("1. 单只股票涨停分析")
    print("2. 多只股票涨停选股")
    print("3. 涨停策略回测")
    print("4. 涨停策略参数优化")
    print("0. 退出")
    print()

    try:
        choice = input("请输入选项 (0-4): ").strip()

        if choice == '1':
            single_stock_analysis()
        elif choice == '2':
            multi_stock_selection()
        elif choice == '3':
            limit_up_backtest()
        elif choice == '4':
            strategy_optimization()
        elif choice == '0':
            print("退出程序")
        else:
            print("无效选项")

    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
    except Exception as e:
        print(f"\n程序出错: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "="*70)
    print("演示完成")
    print("="*70)


if __name__ == '__main__':
    main()
