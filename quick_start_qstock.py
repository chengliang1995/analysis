#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qstock 快速启动脚本
一行命令开始使用
"""

from qstock_selector import QStockSelector
from qstock_strategy_optimizer import StrategyOptimizer
import pandas as pd
import os
import sys


def quick_selection():
    """快速选股"""
    print("\n" + "="*70)
    print("快速选股")
    print("="*70)

    selector = QStockSelector()

    # 获取股票列表
    print("正在获取股票列表...")
    stock_list = selector.get_all_stocks()

    if stock_list.empty:
        print("获取股票列表失败")
        return None

    # 快速选股条件
    selected = selector.multi_filter_select(stock_list, {
        'pe': (10, 30),
        'pb': (1, 5),
        'market_cap': (50, 500)
    })

    if selected.empty:
        print("没有符合条件的股票")
        return None

    # 显示结果
    selector.generate_report(selected, "快速选股结果")

    # 保存结果
    os.makedirs('output', exist_ok=True)
    filename = 'output/quick_selection.csv'
    selector.save_selection(selected, filename)

    return selected


def quick_backtest(selected_stocks=None):
    """快速回测"""
    print("\n" + "="*70)
    print("快速回测")
    print("="*70)

    if selected_stocks is None:
        selected_stocks = quick_selection()
        if selected_stocks is None:
            return

    # 限制数量
    if len(selected_stocks) > 20:
        print(f"股票数量较多({len(selected_stocks)}只)，仅回测前20只")
        selected_stocks = selected_stocks.head(20)

    # 回测
    selector = QStockSelector()
    backtest_result = selector.backtest_strategy(selected_stocks, hold_days=20)

    if not backtest_result.empty:
        # 排序
        backtest_result = backtest_result.sort_values('profit_pct', ascending=False)

        # 显示TOP 10
        print("\n" + "="*70)
        print("收益排名 TOP 10")
        print("="*70)
        print(backtest_result.head(10).to_string())

        # 保存
        filename = 'output/quick_backtest.csv'
        backtest_result.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n回测结果已保存到: {filename}")

        return backtest_result


def quick_technical_analysis():
    """快速技术分析"""
    print("\n" + "="*70)
    print("快速技术分析")
    print("="*70)

    import qstock as qs
    optimizer = StrategyOptimizer()

    # 测试股票
    code = '600519'  # 贵州茅台
    print(f"正在分析股票: {code}")

    # 获取数据
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')

    try:
        hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

        if hist_data.empty:
            print("获取数据失败")
            return

        print(f"成功获取 {len(hist_data)} 条历史数据")

        # 计算技术指标
        print("\n计算技术指标...")
        hist_with_indicators = hist_data.copy()

        # MA
        hist_with_indicators = optimizer.calculate_ma(hist_with_indicators, [5, 10, 20, 60])

        # RSI
        hist_with_indicators = optimizer.calculate_rsi(hist_with_indicators, period=14)

        # MACD
        hist_with_indicators = optimizer.calculate_macd(hist_with_indicators)

        # 最新数据
        latest = hist_with_indicators.iloc[-1]

        print("\n" + "="*70)
        print(f"{code} 最新技术指标")
        print("="*70)
        print(f"日期: {latest.get('date', 'N/A')}")
        print(f"收盘价: {latest['close']:.2f}元")
        print(f"\n均线:")
        print(f"  MA5:   {latest['MA5']:.2f}")
        print(f"  MA10:  {latest['MA10']:.2f}")
        print(f"  MA20:  {latest['MA20']:.2f}")
        print(f"  MA60:  {latest['MA60']:.2f}")
        print(f"\nRSI: {latest['RSI']:.2f}")
        print(f"\nMACD:")
        print(f"  DIF:  {latest['MACD_DIF']:.4f}")
        print(f"  DEA:  {latest['MACD_DEA']:.4f}")
        print(f"  BAR:  {latest['MACD_BAR']:.4f}")

        # 信号分析
        print("\n" + "="*70)
        print("技术信号")
        print("="*70)

        # 均线信号
        if latest['MA5'] > latest['MA20']:
            print("✓ 短期均线多头排列")
        else:
            print("✗ 短期均线空头排列")

        # RSI信号
        if latest['RSI'] < 30:
            print("✓ RSI超卖，可能存在反弹机会")
        elif latest['RSI'] > 70:
            print("✗ RSI超买，可能存在回调风险")
        else:
            print("○ RSI处于正常区间")

        # MACD信号
        if latest['MACD_DIF'] > latest['MACD_DEA'] and latest['MACD_BAR'] > 0:
            print("✓ MACD金叉，看多信号")
        elif latest['MACD_DIF'] < latest['MACD_DEA'] and latest['MACD_BAR'] < 0:
            print("✗ MACD死叉，看空信号")
        else:
            print("○ MACD无明确方向")

        # 保存数据
        os.makedirs('output', exist_ok=True)
        filename = 'output/technical_analysis.csv'
        hist_with_indicators.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n技术分析数据已保存到: {filename}")

    except Exception as e:
        print(f"技术分析失败: {e}")


def quick_optimization():
    """快速参数优化"""
    print("\n" + "="*70)
    print("快速参数优化")
    print("="*70)

    import qstock as qs
    optimizer = StrategyOptimizer()

    # 测试股票
    code = '000001'  # 平安银行
    print(f"正在优化 {code} 的策略参数...")

    # 获取数据
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')

    try:
        hist_data = qs.get_data('hist', code, start=start_date, end=end_date, freq='daily')

        if hist_data.empty:
            print("获取数据失败")
            return

        print(f"成功获取 {len(hist_data)} 条历史数据")

        # 优化均线参数
        print("\n--- 优化均线周期 ---")
        ma_result = optimizer.optimize_ma_periods(
            hist_data,
            short_range=(5, 10),
            long_range=(20, 40),
            step=2
        )

        # 优化RSI参数
        print("\n--- 优化RSI参数 ---")
        rsi_result = optimizer.optimize_rsi_params(
            hist_data,
            oversold_range=(20, 35),
            overbought_range=(65, 80),
            step=5
        )

        print("\n优化完成！")

    except Exception as e:
        print(f"参数优化失败: {e}")


def main():
    """主函数"""
    import sys

    print("="*70)
    print("qstock 快速启动")
    print("="*70)

    # 检查命令行参数
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command == 'select' or command == 's':
            quick_selection()
        elif command == 'backtest' or command == 'b':
            quick_backtest()
        elif command == 'technical' or command == 't':
            quick_technical_analysis()
        elif command == 'optimize' or command == 'o':
            quick_optimization()
        elif command == 'limitup' or command == 'l' or command == 'limit':
            print("\n涨停策略请使用专用脚本:")
            print("  python scan_limit_up_stocks.py")
        elif command == 'daily' or command == 'd':
            import subprocess
            subprocess.run([sys.executable, "daily_advisor.py"])
        elif command == 'web' or command == 'w':
            from web_app import main as run_web
            run_web()
        elif command == 'portfolio' or command == 'p':
            import subprocess
            subprocess.run([sys.executable, "daily_advisor.py", "portfolio"])
        elif command == 'sim':
            import subprocess
            subprocess.run([sys.executable, "daily_advisor.py", "sim", "--force"])
        elif command == 'record' or command == 'r':
            from trade_journal import interactive_record
            interactive_record()
        else:
            print(f"未知命令: {command}")
            print("可用命令: select, backtest, technical, optimize, limitup, daily, web, portfolio, sim, record")
    else:
        # 交互式菜单
        print("\n请选择功能:")
        print("1. 快速选股")
        print("2. 快速回测")
        print("3. 快速技术分析")
        print("4. 快速参数优化")
        print("5. 涨停策略扫描")
        print("6. 每日顾问（超短+学习）")
        print("7. 录入交易记录")
        print("8. Web 仪表盘（持仓+模拟）")
        print("9. 查看个人仓位")
        print("10. 模拟复盘选股")
        print("11. 运行全部（选股/回测/技术/优化）")
        print("0. 退出")
        print()

        try:
            choice = input("请输入选项 (0-11): ").strip()

            if choice == '1':
                quick_selection()
            elif choice == '2':
                quick_backtest()
            elif choice == '3':
                quick_technical_analysis()
            elif choice == '4':
                quick_optimization()
            elif choice == '5':
                print("\n运行涨停策略扫描:")
                import subprocess
                subprocess.run([sys.executable, "scan_limit_up_stocks.py"])
            elif choice == '6':
                import subprocess
                subprocess.run([sys.executable, "daily_advisor.py"])
            elif choice == '7':
                from trade_journal import interactive_record
                interactive_record()
            elif choice == '8':
                from web_app import main as run_web
                run_web()
            elif choice == '9':
                import subprocess
                subprocess.run([sys.executable, "daily_advisor.py", "portfolio"])
            elif choice == '10':
                import subprocess
                subprocess.run([sys.executable, "daily_advisor.py", "sim", "--force"])
            elif choice == '11':
                quick_selection()
                quick_backtest()
                quick_technical_analysis()
                quick_optimization()
            elif choice == '0':
                print("退出程序")
            else:
                print("无效选项")
        except KeyboardInterrupt:
            print("\n\n程序被用户中断")

    print("\n" + "="*70)
    print("完成")
    print("="*70)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n程序出错: {e}")
        import traceback
        traceback.print_exc()
