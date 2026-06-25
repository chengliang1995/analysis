"""
qstock 快速启动脚本
展示如何使用 qstock 进行选股、策略验证和优化
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantpy.qstock_selector import QStockSelector
from quantpy.qstock_strategy_optimizer import StrategyOptimizer
import pandas as pd
from datetime import datetime, timedelta


def demo_basic_selection():
    """基础选股演示"""
    print("\n" + "="*70)
    print("演示1: 基础选股")
    print("="*70)

    selector = QStockSelector()

    # 获取股票列表
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("无法获取股票列表，请检查网络连接")
        return

    # PE选股
    print("\n--- PE选股 (10-30) ---")
    selected_pe = selector.select_by_pe(stock_list, min_pe=10, max_pe=30)
    if not selected_pe.empty:
        selector.save_selection(selected_pe, 'output/selected_pe.csv')

    # PB选股
    print("\n--- PB选股 (0-5) ---")
    selected_pb = selector.select_by_pb(stock_list, min_pb=0, max_pb=5)
    if not selected_pb.empty:
        selector.save_selection(selected_pb, 'output/selected_pb.csv')

    # 市值选股
    print("\n--- 市值选股 (50-500亿) ---")
    selected_cap = selector.select_by_market_cap(stock_list, min_cap=50, max_cap=500)
    if not selected_cap.empty:
        selector.save_selection(selected_cap, 'output/selected_cap.csv')


def demo_multi_filter():
    """多条件组合选股演示"""
    print("\n" + "="*70)
    print("演示2: 多条件组合选股")
    print("="*70)

    selector = QStockSelector()

    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("无法获取股票列表")
        return

    # 多条件选股
    filters = {
        'pe': (10, 30),
        'pb': (1, 5),
        'market_cap': (50, 300)
    }

    selected = selector.multi_filter_select(stock_list, filters)

    if not selected.empty:
        selector.generate_report(selected, "多条件选股报告")
        selector.save_selection(selected, 'output/selected_multi_filter.csv')

        # 简单回测
        if len(selected) <= 30:
            print("\n开始回测...")
            backtest_result = selector.backtest_strategy(selected, hold_days=20)
            if not backtest_result.empty:
                backtest_result.to_csv('output/backtest_result.csv', index=False)


def demo_technical_strategy():
    """技术指标策略演示"""
    print("\n" + "="*70)
    print("演示3: 技术指标策略")
    print("="*70)

    optimizer = StrategyOptimizer()

    # 获取测试数据
    code = '600519'  # 贵州茅台
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
    end_date = datetime.now().strftime('%Y%m%d')

    print(f"正在获取 {code} 的历史数据...")
    import qstock as qs

    try:
        hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

        if hist_data.empty:
            print("获取数据失败")
            return

        print(f"成功获取 {len(hist_data)} 条数据")

        # 计算技术指标
        print("\n--- 计算技术指标 ---")
        hist_with_ma = optimizer.calculate_ma(hist_data, [5, 10, 20, 60])
        hist_with_rsi = optimizer.calculate_rsi(hist_data, period=14)
        hist_with_macd = optimizer.calculate_macd(hist_data)

        # 显示最新数据
        print("\n最新数据（含技术指标）:")
        latest = hist_with_macd.iloc[-1]
        print(f"日期: {latest.get('date', 'N/A')}")
        print(f"收盘价: {latest['close']:.2f}")
        print(f"MACD DIF: {latest['MACD_DIF']:.4f}")
        print(f"MACD DEA: {latest['MACD_DEA']:.4f}")
        print(f"MACD BAR: {latest['MACD_BAR']:.4f}")

    except Exception as e:
        print(f"操作失败: {e}")


def demo_optimization():
    """参数优化演示"""
    print("\n" + "="*70)
    print("演示4: 策略参数优化")
    print("="*70)

    optimizer = StrategyOptimizer()

    # 获取测试数据
    code = '000001'  # 平安银行
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
    end_date = datetime.now().strftime('%Y%m%d')

    print(f"正在获取 {code} 的历史数据用于优化...")
    import qstock as qs

    try:
        hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

        if hist_data.empty:
            print("获取数据失败")
            return

        print(f"成功获取 {len(hist_data)} 条数据")

        # 优化均线参数
        print("\n--- 优化均线参数 ---")
        ma_opt_result = optimizer.optimize_ma_periods(
            hist_data,
            short_range=(5, 10),
            long_range=(20, 40),
            step=2
        )

        # 优化RSI参数
        print("\n--- 优化RSI参数 ---")
        rsi_opt_result = optimizer.optimize_rsi_params(
            hist_data,
            oversold_range=(20, 35),
            overbought_range=(65, 80),
            step=5
        )

    except Exception as e:
        print(f"优化失败: {e}")


def demo_multi_stock_backtest():
    """多股票回测演示"""
    print("\n" + "="*70)
    print("演示5: 多股票策略回测")
    print("="*70)

    selector = QStockSelector()
    optimizer = StrategyOptimizer()

    # 获取股票列表并选股
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("无法获取股票列表")
        return

    # 选出符合条件的股票
    selected = selector.multi_filter_select(stock_list, {
        'pe': (10, 30),
        'market_cap': (50, 500)
    })

    if selected.empty:
        print("没有符合条件的股票")
        return

    # 限制数量用于演示
    demo_stocks = selected.head(10)
    print(f"\n选择 {len(demo_stocks)} 只股票进行回测演示")

    # 回测
    backtest_result = selector.backtest_strategy(demo_stocks, hold_days=20)

    if not backtest_result.empty:
        # 排序显示
        backtest_result = backtest_result.sort_values('profit_pct', ascending=False)

        print("\n" + "="*70)
        print("回测结果排名")
        print("="*70)
        print(backtest_result.head(10).to_string())

        # 保存结果
        import os
        os.makedirs('output', exist_ok=True)
        backtest_result.to_csv('output/multi_stock_backtest.csv', index=False)
        print("\n结果已保存到: output/multi_stock_backtest.csv")


def demo_comprehensive():
    """综合演示：选股 + 技术分析 + 回测"""
    print("\n" + "="*70)
    print("演示6: 综合策略演示")
    print("="*70)

    selector = QStockSelector()
    optimizer = StrategyOptimizer()

    # 步骤1: 选股
    print("\n步骤1: 基本面选股")
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("无法获取股票列表")
        return

    selected = selector.multi_filter_select(stock_list, {
        'pe': (10, 30),
        'pb': (1, 5),
        'market_cap': (50, 300)
    })

    if selected.empty or len(selected) < 5:
        print("选股结果不足，跳过后续步骤")
        return

    print(f"\n选出 {len(selected)} 只股票")

    # 步骤2: 对前10只进行技术分析
    print("\n步骤2: 技术指标筛选")
    demo_stocks = selected.head(10)
    import qstock as qs

    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
    end_date = datetime.now().strftime('%Y%m%d')

    final_selection = []

    for idx, stock in demo_stocks.iterrows():
        code = stock.get('code', '')
        if not code:
            continue

        try:
            hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

            if hist_data.empty:
                continue

            # 应用多信号策略
            hist_with_signals = optimizer.multi_signal_strategy(
                hist_data,
                strategies=['MA_CROSS', 'RSI_BUY'],
                min_signals=1
            )

            # 检查最新是否有买入信号
            latest_signal = hist_with_signals.iloc[-1]['BUY']

            if latest_signal == 1:
                final_selection.append(stock)
                print(f"  ✓ {code} ({stock.get('name', '')}): 技术面符合")

        except Exception as e:
            continue

    print(f"\n技术面筛选后剩余 {len(final_selection)} 只股票")

    # 步骤3: 回测验证
    if final_selection:
        print("\n步骤3: 回测验证")
        final_df = pd.DataFrame(final_selection)
        backtest_result = selector.backtest_strategy(final_df, hold_days=20)

        if not backtest_result.empty:
            backtest_result.to_csv('output/comprehensive_strategy_result.csv', index=False)
    else:
        print("没有股票通过技术面筛选")


def main():
    """主函数"""
    print("="*70)
    print("qstock 选股与策略验证系统")
    print("="*70)
    print()
    print("请选择要运行的演示:")
    print("1. 基础选股演示")
    print("2. 多条件组合选股")
    print("3. 技术指标策略")
    print("4. 策略参数优化")
    print("5. 多股票回测")
    print("6. 综合策略演示")
    print("7. 运行全部演示")
    print("0. 退出")
    print()

    choice = input("请输入选项 (0-7): ").strip()

    if choice == '1':
        demo_basic_selection()
    elif choice == '2':
        demo_multi_filter()
    elif choice == '3':
        demo_technical_strategy()
    elif choice == '4':
        demo_optimization()
    elif choice == '5':
        demo_multi_stock_backtest()
    elif choice == '6':
        demo_comprehensive()
    elif choice == '7':
        demo_basic_selection()
        demo_multi_filter()
        demo_technical_strategy()
        demo_optimization()
        demo_multi_stock_backtest()
        demo_comprehensive()
    elif choice == '0':
        print("退出程序")
    else:
        print("无效选项")

    print("\n" + "="*70)
    print("演示完成")
    print("="*70)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
    except Exception as e:
        print(f"\n程序出错: {e}")
        import traceback
        traceback.print_exc()
