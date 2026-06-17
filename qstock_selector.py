"""
基于 qstock 的股票选股和策略验证系统
支持多种选股策略、回测验证和优化学习
"""

import qstock as qs
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Callable, Optional
import warnings

from stock_data import get_all_stocks as fetch_all_stocks, get_stock_hist

warnings.filterwarnings('ignore')


class QStockSelector:
    """基于 qstock 的选股系统"""

    def __init__(self):
        """初始化选股器"""
        self.today = datetime.now().strftime('%Y%m%d')
        print(f"选股系统初始化完成 - 当前日期: {self.today}")

    def get_all_stocks(self) -> pd.DataFrame:
        """获取所有A股股票基本信息（多数据源自动降级）。"""
        print("正在获取股票基本信息...")
        stock_list = fetch_all_stocks()
        if stock_list.empty:
            print("获取股票列表失败")
            return stock_list
        print(f"成功获取 {len(stock_list)} 只股票")
        return stock_list

    def get_stock_realtime(self, codes: List[str]) -> pd.DataFrame:
        """
        获取股票实时行情

        Args:
            codes: 股票代码列表

        Returns:
            实时行情数据
        """
        try:
            print(f"正在获取 {len(codes)} 只股票的实时行情...")
            data = qs.get_data('realtime', codes)
            print(f"成功获取实时数据")
            return data
        except Exception as e:
            print(f"获取实时行情失败: {e}")
            return pd.DataFrame()

    def get_stock_hist(self, code: str, start: str, end: str,
                      freq: str = 'daily') -> pd.DataFrame:
        """获取历史行情数据。"""
        freq_map = {'daily': 'd', 'weekly': 'w', 'monthly': 'm'}
        q_freq = freq_map.get(freq, 'd')
        try:
            data = get_stock_hist(code, start=start, end=end, freq=q_freq)
            if not data.empty:
                return data
            return qs.get_data('hist', code, start=start, end=end, freq=freq)
        except Exception as e:
            print(f"获取 {code} 历史数据失败: {e}")
            return pd.DataFrame()

    def get_stock_fundamental(self, codes: List[str]) -> pd.DataFrame:
        """
        获取股票基本面数据

        Args:
            codes: 股票代码列表

        Returns:
            基本面数据
        """
        try:
            print(f"正在获取 {len(codes)} 只股票的基本面数据...")
            data = qs.get_data('financial_indicator', codes)
            return data
        except Exception as e:
            print(f"获取基本面数据失败: {e}")
            return pd.DataFrame()

    # ==================== 选股策略 ====================

    def select_by_pe(self, stock_list: pd.DataFrame, min_pe: float = 0,
                     max_pe: float = 50) -> pd.DataFrame:
        """
        根据PE市盈率选股

        Args:
            stock_list: 股票列表
            min_pe: 最小PE
            max_pe: 最大PE

        Returns:
            选中的股票
        """
        if 'pe' not in stock_list.columns:
            print("警告: 股票列表中没有PE数据，跳过PE选股")
            return pd.DataFrame()

        selected = stock_list[
            (stock_list['pe'] > min_pe) &
            (stock_list['pe'] < max_pe)
        ].copy()

        selected = selected.sort_values('pe', ascending=True)
        print(f"PE选股: {len(selected)} 只股票 (PE: {min_pe}-{max_pe})")
        return selected

    def select_by_pb(self, stock_list: pd.DataFrame, min_pb: float = 0,
                     max_pb: float = 10) -> pd.DataFrame:
        """
        根据PB市净率选股

        Args:
            stock_list: 股票列表
            min_pb: 最小PB
            max_pb: 最大PB

        Returns:
            选中的股票
        """
        if 'pb' not in stock_list.columns:
            print("警告: 股票列表中没有PB数据，跳过PB选股")
            return pd.DataFrame()

        selected = stock_list[
            (stock_list['pb'] > min_pb) &
            (stock_list['pb'] < max_pb)
        ].copy()

        selected = selected.sort_values('pb', ascending=True)
        print(f"PB选股: {len(selected)} 只股票 (PB: {min_pb}-{max_pb})")
        return selected

    def select_by_market_cap(self, stock_list: pd.DataFrame,
                             min_cap: float = 0,
                             max_cap: float = 1000) -> pd.DataFrame:
        """
        根据市值选股

        Args:
            stock_list: 股票列表
            min_cap: 最小市值（亿元）
            max_cap: 最大市值（亿元）

        Returns:
            选中的股票
        """
        if 'market_cap' not in stock_list.columns:
            print("警告: 股票列表中没有市值数据，跳过市值选股")
            return pd.DataFrame()

        selected = stock_list[
            (stock_list['market_cap'] > min_cap) &
            (stock_list['market_cap'] < max_cap)
        ].copy()

        selected = selected.sort_values('market_cap', ascending=True)
        print(f"市值选股: {len(selected)} 只股票 (市值: {min_cap}-{max_cap}亿)")
        return selected

    def select_by_roe(self, stock_list: pd.DataFrame, min_roe: float = 10) -> pd.DataFrame:
        """
        根据ROE净资产收益率选股

        Args:
            stock_list: 股票列表（包含基本面数据）
            min_roe: 最小ROE（%）

        Returns:
            选中的股票
        """
        if 'roe' not in stock_list.columns:
            print("警告: 股票列表中没有ROE数据，跳过ROE选股")
            return pd.DataFrame()

        selected = stock_list[
            (stock_list['roe'] > min_roe)
        ].copy()

        selected = selected.sort_values('roe', ascending=False)
        print(f"ROE选股: {len(selected)} 只股票 (ROE > {min_roe}%)")
        return selected

    def select_by_price(self, stock_list: pd.DataFrame,
                        min_price: float = 0,
                        max_price: float = 100) -> pd.DataFrame:
        """
        根据股价选股

        Args:
            stock_list: 股票列表
            min_price: 最低价格
            max_price: 最高价格

        Returns:
            选中的股票
        """
        if 'price' not in stock_list.columns:
            print("警告: 股票列表中没有价格数据，跳过价格选股")
            return pd.DataFrame()

        selected = stock_list[
            (stock_list['price'] >= min_price) &
            (stock_list['price'] <= max_price)
        ].copy()

        selected = selected.sort_values('price', ascending=True)
        print(f"价格选股: {len(selected)} 只股票 (价格: {min_price}-{max_price}元)")
        return selected

    def select_by_turnover(self, stock_list: pd.DataFrame,
                          min_turnover: float = 2,
                          max_turnover: float = 20) -> pd.DataFrame:
        """
        根据换手率选股

        Args:
            stock_list: 股票列表
            min_turnover: 最小换手率（%）
            max_turnover: 最大换手率（%）

        Returns:
            选中的股票
        """
        if 'turnover' not in stock_list.columns:
            print("警告: 股票列表中没有换手率数据，跳过换手率选股")
            return pd.DataFrame()

        selected = stock_list[
            (stock_list['turnover'] >= min_turnover) &
            (stock_list['turnover'] <= max_turnover)
        ].copy()

        selected = selected.sort_values('turnover', ascending=False)
        print(f"换手率选股: {len(selected)} 只股票 (换手率: {min_turnover}-{max_turnover}%)")
        return selected

    def multi_filter_select(self, stock_list: pd.DataFrame,
                           filters: Dict[str, Tuple]) -> pd.DataFrame:
        """
        多条件组合选股

        Args:
            stock_list: 股票列表
            filters: 筛选条件字典
                例如: {'pe': (0, 30), 'pb': (0, 5), 'market_cap': (50, 500)}

        Returns:
            选中的股票
        """
        result = stock_list.copy()

        for field, (min_val, max_val) in filters.items():
            if field not in result.columns:
                print(f"警告: 股票列表中没有 {field} 字段，跳过该条件")
                continue

            result = result[
                (result[field] >= min_val) &
                (result[field] <= max_val)
            ]

        print(f"多条件选股: {len(result)} 只股票")
        print(f"筛选条件: {filters}")
        return result

    # ==================== 策略验证 ====================

    def backtest_strategy(self, selected_stocks: pd.DataFrame,
                          hold_days: int = 20,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None) -> pd.DataFrame:
        """
        简单的持有期回测

        Args:
            selected_stocks: 选中的股票
            hold_days: 持有天数
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            回测结果
        """
        if start_date is None:
            start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        if end_date is None:
            end_date = self.today

        results = []

        for idx, stock in selected_stocks.iterrows():
            code = stock.get('code', stock.get('代码', ''))
            if not code:
                continue

            try:
                hist_data = self.get_stock_hist(str(code).zfill(6), start_date, end_date)

                if hist_data.empty or len(hist_data) < hold_days:
                    continue

                hist_data = hist_data.sort_values('date') if 'date' in hist_data.columns else hist_data
                buy_price = hist_data.iloc[0]['close']

                if len(hist_data) > hold_days:
                    sell_price = hist_data.iloc[hold_days]['close']
                else:
                    sell_price = hist_data.iloc[-1]['close']

                profit_pct = (sell_price - buy_price) / buy_price * 100

                results.append({
                    'code': code,
                    'name': stock.get('name', stock.get('名称', '')),
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'profit_pct': profit_pct,
                    'hold_days': min(hold_days, len(hist_data))
                })

            except Exception as e:
                print(f"回测 {code} 失败: {e}")
                continue

        if results:
            result_df = pd.DataFrame(results)
            print(f"\n回测结果 ({hold_days}天持有期):")
            print(f"  平均收益: {result_df['profit_pct'].mean():.2f}%")
            print(f"  最高收益: {result_df['profit_pct'].max():.2f}%")
            print(f"  最低收益: {result_df['profit_pct'].min():.2f}%")
            print(f"  胜率: {(result_df['profit_pct'] > 0).sum() / len(result_df) * 100:.1f}%")
            return result_df
        else:
            print("回测完成，无有效数据")
            return pd.DataFrame()

    def optimize_parameters(self, stock_list: pd.DataFrame,
                           strategy_func: Callable,
                           param_ranges: Dict[str, List],
                           metric: str = 'profit_pct') -> Dict:
        """
        参数优化

        Args:
            stock_list: 股票列表
            strategy_func: 策略函数
            param_ranges: 参数范围字典
            metric: 优化指标

        Returns:
            最优参数
        """
        best_result = None
        best_score = -float('inf')
        best_params = {}

        # 生成参数组合
        param_names = list(param_ranges.keys())
        param_values = list(param_ranges.values())

        from itertools import product

        for combination in product(*param_values):
            params = dict(zip(param_names, combination))

            try:
                # 执行选股策略
                selected = strategy_func(stock_list, **params)

                if selected.empty or len(selected) < 5:
                    continue

                # 简单回测
                backtest_result = self.backtest_strategy(selected, hold_days=20)

                if backtest_result.empty:
                    continue

                # 评估
                score = backtest_result[metric].mean()

                if score > best_score:
                    best_score = score
                    best_result = backtest_result
                    best_params = params
                    print(f"发现更优参数: {params}, 平均收益: {score:.2f}%")

            except Exception as e:
                print(f"参数 {params} 测试失败: {e}")
                continue

        print(f"\n最优参数: {best_params}")
        print(f"最优收益: {best_score:.2f}%")

        return {
            'params': best_params,
            'result': best_result,
            'score': best_score
        }

    def save_selection(self, selected_stocks: pd.DataFrame, filename: str):
        """保存选股结果"""
        selected_stocks.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"选股结果已保存到: {filename}")

    def generate_report(self, selected_stocks: pd.DataFrame, title: str = "选股报告"):
        """生成选股报告"""
        print("\n" + "="*60)
        print(title)
        print("="*60)
        print(f"选股数量: {len(selected_stocks)} 只")
        print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        if not selected_stocks.empty:
            print("\n前10只股票:")
            print(selected_stocks.head(10).to_string())

            # 统计分析
            if 'pe' in selected_stocks.columns:
                print(f"\nPE统计:")
                print(f"  平均: {selected_stocks['pe'].mean():.2f}")
                print(f"  中位: {selected_stocks['pe'].median():.2f}")
                print(f"  最小: {selected_stocks['pe'].min():.2f}")
                print(f"  最大: {selected_stocks['pe'].max():.2f}")

            if 'market_cap' in selected_stocks.columns:
                print(f"\n市值统计:")
                print(f"  平均: {selected_stocks['market_cap'].mean():.2f}亿")
                print(f"  中位: {selected_stocks['market_cap'].median():.2f}亿")


if __name__ == '__main__':
    # 创建选股器
    selector = QStockSelector()

    # 示例1: 简单PE选股
    print("\n" + "="*60)
    print("示例1: PE选股")
    print("="*60)

    stock_list = selector.get_all_stocks()
    if not stock_list.empty:
        selected = selector.select_by_pe(stock_list, min_pe=10, max_pe=30)
        selector.generate_report(selected, "PE选股结果")
        selector.save_selection(selected, 'selected_by_pe.csv')

        # 示例2: 多条件选股
        print("\n" + "="*60)
        print("示例2: 多条件选股")
        print("="*60)

        selected_multi = selector.multi_filter_select(stock_list, {
            'pe': (0, 30),
            'pb': (0, 5),
            'market_cap': (50, 500)
        })
        selector.generate_report(selected_multi, "多条件选股结果")
        selector.save_selection(selected_multi, 'selected_multi.csv')

        # 示例3: 策略回测
        print("\n" + "="*60)
        print("示例3: 策略回测")
        print("="*60)

        if not selected_multi.empty and len(selected_multi) <= 50:
            backtest_result = selector.backtest_strategy(selected_multi, hold_days=20)
            if not backtest_result.empty:
                backtest_result.to_csv('backtest_result.csv', index=False, encoding='utf-8-sig')
        else:
            print("股票数量过多，跳过回测")
