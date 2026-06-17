"""
qstock 策略优化和学习系统
支持多种技术指标策略、参数优化和回测
"""

import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings

from stock_data import (
    get_all_stocks as fetch_all_stocks,
    get_stock_code_column,
    get_stock_hist,
    get_stock_name_column,
)

warnings.filterwarnings('ignore')


class StrategyOptimizer:
    """策略优化器"""

    def __init__(self):
        self.today = datetime.now().strftime('%Y%m%d')
        print(f"策略优化器初始化完成")

    # ==================== 技术指标计算 ====================

    def calculate_ma(self, df: pd.DataFrame, periods: List[int] = [5, 10, 20, 60]) -> pd.DataFrame:
        """
        计算移动平均线

        Args:
            df: 价格数据，需包含 'close' 列
            periods: 周期列表

        Returns:
            包含MA的DataFrame
        """
        result = df.copy()
        for period in periods:
            result[f'MA{period}'] = df['close'].rolling(window=period).mean()
        return result

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        计算RSI相对强弱指标

        Args:
            df: 价格数据
            period: 周期

        Returns:
            包含RSI的DataFrame
        """
        result = df.copy()

        # 计算价格变化
        delta = df['close'].diff()

        # 分离涨跌
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # 计算平均涨跌
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        # 计算RSI
        rs = avg_gain / avg_loss
        result['RSI'] = 100 - (100 / (1 + rs))

        return result

    def calculate_macd(self, df: pd.DataFrame,
                       fast: int = 12,
                       slow: int = 26,
                       signal: int = 9) -> pd.DataFrame:
        """
        计算MACD指标

        Args:
            df: 价格数据
            fast: 快线周期
            slow: 慢线周期
            signal: 信号线周期

        Returns:
            包含MACD的DataFrame
        """
        result = df.copy()

        # 计算EMA
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()

        # 计算DIF和DEA
        result['MACD_DIF'] = ema_fast - ema_slow
        result['MACD_DEA'] = result['MACD_DIF'].ewm(span=signal, adjust=False).mean()
        result['MACD_BAR'] = (result['MACD_DIF'] - result['MACD_DEA']) * 2

        return result

    def calculate_bollinger(self, df: pd.DataFrame,
                            period: int = 20,
                            std_dev: int = 2) -> pd.DataFrame:
        """
        计算布林带

        Args:
            df: 价格数据
            period: 周期
            std_dev: 标准差倍数

        Returns:
            包含布林带的DataFrame
        """
        result = df.copy()

        # 计算中轨（移动平均）
        result['BB_MID'] = df['close'].rolling(window=period).mean()

        # 计算标准差
        std = df['close'].rolling(window=period).std()

        # 计算上下轨
        result['BB_UPPER'] = result['BB_MID'] + std * std_dev
        result['BB_LOWER'] = result['BB_MID'] - std * std_dev

        return result

    def calculate_kdj(self, df: pd.DataFrame,
                      n: int = 9,
                      m1: int = 3,
                      m2: int = 3) -> pd.DataFrame:
        """
        计算KDJ指标

        Args:
            df: 价格数据，需包含 'high', 'low', 'close'
            n: 周期
            m1: K值平滑系数
            m2: D值平滑系数

        Returns:
            包含KDJ的DataFrame
        """
        result = df.copy()

        # 计算RSV
        low_n = df['low'].rolling(window=n).min()
        high_n = df['high'].rolling(window=n).max()
        rsv = (df['close'] - low_n) / (high_n - low_n) * 100

        # 计算KDJ
        result['KDJ_K'] = rsv.ewm(com=m1 - 1, adjust=False).mean()
        result['KDJ_D'] = result['KDJ_K'].ewm(com=m2 - 1, adjust=False).mean()
        result['KDJ_J'] = 3 * result['KDJ_K'] - 2 * result['KDJ_D']

        return result

    # ==================== 选股策略 ====================

    def ma_cross_strategy(self, df: pd.DataFrame,
                          short_period: int = 5,
                          long_period: int = 20) -> pd.DataFrame:
        """
        均线交叉选股策略

        Args:
            df: 价格数据
            short_period: 短期均线
            long_period: 长期均线

        Returns:
            信号DataFrame
        """
        df_with_ma = self.calculate_ma(df, [short_period, long_period])
        df_with_ma['MA_CROSS'] = (
            (df_with_ma[f'MA{short_period}'] > df_with_ma[f'MA{long_period}'])
        ).astype(int)

        return df_with_ma

    def rsi_strategy(self, df: pd.DataFrame,
                     oversold: int = 30,
                     overbought: int = 70) -> pd.DataFrame:
        """
        RSI超卖超买选股策略

        Args:
            df: 价格数据
            oversold: 超卖阈值
            overbought: 超买阈值

        Returns:
            信号DataFrame
        """
        df_with_rsi = self.calculate_rsi(df)
        df_with_rsi['RSI_BUY'] = (df_with_rsi['RSI'] < oversold).astype(int)
        df_with_rsi['RSI_SELL'] = (df_with_rsi['RSI'] > overbought).astype(int)

        return df_with_rsi

    def macd_strategy(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        MACD金叉死叉选股策略

        Args:
            df: 价格数据

        Returns:
            信号DataFrame
        """
        df_with_macd = self.calculate_macd(df)

        # 金叉：DIF上穿DEA
        df_with_macd['MACD_GOLDEN'] = (
            (df_with_macd['MACD_DIF'] > df_with_macd['MACD_DEA']) &
            (df_with_macd['MACD_DIF'].shift(1) <= df_with_macd['MACD_DEA'].shift(1))
        ).astype(int)

        # 死叉：DIF下穿DEA
        df_with_macd['MACD_DEAD'] = (
            (df_with_macd['MACD_DIF'] < df_with_macd['MACD_DEA']) &
            (df_with_macd['MACD_DIF'].shift(1) >= df_with_macd['MACD_DEA'].shift(1))
        ).astype(int)

        return df_with_macd

    def bollinger_strategy(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        布林带突破选股策略

        Args:
            df: 价格数据

        Returns:
            信号DataFrame
        """
        df_with_bb = self.calculate_bollinger(df)

        # 价格触及下轨
        df_with_bb['BB_OVERSOLD'] = (df_with_bb['close'] <= df_with_bb['BB_LOWER']).astype(int)

        # 价格触及上轨
        df_with_bb['BB_OVERBOUGHT'] = (df_with_bb['close'] >= df_with_bb['BB_UPPER']).astype(int)

        # 收盘价突破中轨
        df_with_bb['BB_BREAKOUT'] = (
            (df_with_bb['close'] > df_with_bb['BB_MID']) &
            (df_with_bb['close'].shift(1) <= df_with_bb['BB_MID'].shift(1))
        ).astype(int)

        return df_with_bb

    def kdj_strategy(self, df: pd.DataFrame,
                     oversold: int = 20,
                     overbought: int = 80) -> pd.DataFrame:
        """
        KDJ超卖超买选股策略

        Args:
            df: 价格数据
            oversold: 超卖阈值
            overbought: 超买阈值

        Returns:
            信号DataFrame
        """
        df_with_kdj = self.calculate_kdj(df)

        df_with_kdj['KDJ_BUY'] = (df_with_kdj['KDJ_K'] < oversold).astype(int)
        df_with_kdj['KDJ_SELL'] = (df_with_kdj['KDJ_K'] > overbought).astype(int)

        # KDJ金叉
        df_with_kdj['KDJ_GOLDEN'] = (
            (df_with_kdj['KDJ_K'] > df_with_kdj['KDJ_D']) &
            (df_with_kdj['KDJ_K'].shift(1) <= df_with_kdj['KDJ_D'].shift(1))
        ).astype(int)

        return df_with_kdj

    def _calc_pct_chg(self, df: pd.DataFrame) -> pd.Series:
        if 'pct_chg' in df.columns:
            return pd.to_numeric(df['pct_chg'], errors='coerce')
        return df['close'].pct_change() * 100

    def check_limit_up_signal(self, df: pd.DataFrame,
                              lookback_days: int = 10) -> Optional[Dict]:
        """
        快速检测最新交易日是否满足涨停策略（扫描全市场时使用）。

        策略：lookback_days 内有涨停，且最新收盘价未跌破涨停日开盘价。
        """
        if df.empty or len(df) < lookback_days:
            return None

        pct_chg = self._calc_pct_chg(df)
        recent = df.iloc[-lookback_days:].copy()
        recent_pct = pct_chg.iloc[-lookback_days:]
        limit_mask = recent_pct >= 9.8

        if not limit_mask.any():
            return None

        limit_pos = limit_mask[limit_mask].index[-1]
        limit_day = df.loc[limit_pos]
        latest = df.iloc[-1]

        if latest['close'] < limit_day['open'] * 0.995:
            return None

        limit_idx = df.index.get_loc(limit_pos)
        if isinstance(limit_idx, slice):
            limit_idx = limit_idx.start or 0

        limit_date = limit_day.get('date', limit_pos)
        return {
            'limit_date': limit_date,
            'limit_pct': float(recent_pct.loc[limit_pos]),
            'limit_high': float(limit_day['high']),
            'limit_open': float(limit_day['open']),
            'limit_close': float(limit_day['close']),
            'latest_close': float(latest['close']),
            'latest_pct': float(pct_chg.iloc[-1]) if pd.notna(pct_chg.iloc[-1]) else 0.0,
            'days_after_limit': len(df) - int(limit_idx) - 1,
            'limit_up_idx': int(limit_idx),
        }

    def limit_up_strategy(self, df: pd.DataFrame,
                          lookback_days: int = 10) -> pd.DataFrame:
        """
        涨停板选股策略
        10个交易日内有涨停，且最近一个交易日的收盘价未跌破涨停当天的开盘价格

        Args:
            df: 价格数据，需包含 'open', 'high', 'low', 'close'
            lookback_days: 回溯天数

        Returns:
            信号DataFrame
        """
        result = df.copy()
        pct_chg = self._calc_pct_chg(result)
        result['pct_chg'] = pct_chg
        result['is_limit_up'] = (pct_chg >= 9.8).astype(int)
        result['has_limit_up'] = (
            result['is_limit_up'].rolling(window=lookback_days, min_periods=1).max()
        ).astype(int)

        opens = result['open'].to_numpy()
        closes = result['close'].to_numpy()
        is_limit = result['is_limit_up'].to_numpy()
        n = len(result)

        limit_up_idx = np.zeros(n, dtype=int)
        above_limit_open = np.zeros(n, dtype=int)

        for i in range(n):
            start = max(0, i - lookback_days + 1)
            window = is_limit[start:i + 1]
            if not window.any():
                continue
            rel_idx = np.where(window)[0][-1]
            idx = start + rel_idx
            limit_up_idx[i] = idx
            if closes[i] >= opens[idx] * 0.995:
                above_limit_open[i] = 1

        result['limit_up_idx'] = limit_up_idx
        result['above_limit_open'] = above_limit_open
        result['LIMIT_UP_SIGNAL'] = (
            (result['has_limit_up'] == 1) & (result['above_limit_open'] == 1)
        ).astype(int)

        return result

    def get_stock_data(self, code: str, days: int = 180) -> pd.DataFrame:
        """获取股票历史数据。"""
        return get_stock_hist(code, days=days)

    def get_all_stocks(self) -> pd.DataFrame:
        """获取所有 A 股列表（多数据源自动降级）。"""
        return fetch_all_stocks()

    def _scan_single_limit_up(self, code: str, name: str,
                              lookback_days: int) -> Optional[Dict]:
        hist_data = self.get_stock_data(code, days=lookback_days + 30)
        signal = self.check_limit_up_signal(hist_data, lookback_days)
        if not signal:
            return None
        return {'code': code, 'name': name, **signal}

    def find_limit_up_stocks(self, stock_list: pd.DataFrame = None,
                             lookback_days: int = 10,
                             max_workers: int = 8,
                             show_progress: bool = True) -> pd.DataFrame:
        """
        从股票列表中筛选符合条件的涨停股票。

        Args:
            stock_list: 股票列表，None 时自动获取全市场
            lookback_days: 回溯交易日数
            max_workers: 并发线程数（网络 IO 密集，默认 8）
            show_progress: 是否打印进度
        """
        if stock_list is None:
            stock_list = self.get_all_stocks()

        if stock_list.empty:
            print("股票列表为空")
            return pd.DataFrame()

        code_col = get_stock_code_column(stock_list)
        name_col = get_stock_name_column(stock_list)
        tasks = []
        for _, stock in stock_list.iterrows():
            code = str(stock.get(code_col, '')).strip()
            if not code or code == 'nan':
                continue
            name = stock.get(name_col, '') if name_col else ''
            tasks.append((code.zfill(6), name))

        results: List[Dict] = []
        total = len(tasks)
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._scan_single_limit_up, code, name, lookback_days): code
                for code, name in tasks
            }
            for future in as_completed(futures):
                done += 1
                if show_progress and done % 100 == 0:
                    print(f"扫描进度: {done}/{total}")
                try:
                    item = future.result()
                    if item:
                        results.append(item)
                except Exception as exc:
                    code = futures[future]
                    if show_progress:
                        print(f"处理 {code} 失败: {exc}")

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)
        return result_df.sort_values('limit_date', ascending=False)

    # ==================== 回测系统 ====================

    def backtest_signal(self, df: pd.DataFrame,
                       signal_column: str,
                       hold_days: int = 20) -> pd.DataFrame:
        """
        基于信号回测

        Args:
            df: 包含信号的数据
            signal_column: 信号列名
            hold_days: 持有天数

        Returns:
            回测结果
        """
        signals = df[df[signal_column] == 1].copy()

        results = []

        for idx, signal in signals.iterrows():
            buy_date = signal.get('date', idx)
            buy_price = signal['close']

            # 找到卖出点
            sell_date = None
            sell_price = None
            actual_hold_days = 0

            # 在之后的数据中找到卖出点
            future_data = df.loc[idx+1: idx+hold_days]

            if not future_data.empty:
                sell_date = future_data.index[0] if hasattr(future_data.index[0], 'date') else buy_date
                sell_price = future_data.iloc[0]['close']
                actual_hold_days = 1

            if sell_price is not None:
                profit_pct = (sell_price - buy_price) / buy_price * 100

                results.append({
                    'buy_date': buy_date,
                    'sell_date': sell_date,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'profit_pct': profit_pct,
                    'hold_days': actual_hold_days
                })

        if results:
            result_df = pd.DataFrame(results)

            print(f"\n{signal_column} 策略回测结果:")
            print(f"  信号次数: {len(result_df)}")
            print(f"  平均收益: {result_df['profit_pct'].mean():.2f}%")
            print(f"  最高收益: {result_df['profit_pct'].max():.2f}%")
            print(f"  最低收益: {result_df['profit_pct'].min():.2f}%")
            print(f"  胜率: {(result_df['profit_pct'] > 0).sum() / len(result_df) * 100:.1f}%")

            return result_df
        else:
            print(f"{signal_column} 策略无信号")
            return pd.DataFrame()

    # ==================== 参数优化 ====================

    def optimize_ma_periods(self, df: pd.DataFrame,
                            short_range: Tuple[int, int] = (5, 15),
                            long_range: Tuple[int, int] = (20, 60),
                            step: int = 5) -> Dict:
        """
        优化均线周期参数

        Args:
            df: 价格数据
            short_range: 短期均线范围
            long_range: 长期均线范围
            step: 步长

        Returns:
            最优参数和结果
        """
        best_result = None
        best_score = -float('inf')
        best_params = {}

        print("开始优化均线周期参数...")

        for short in range(short_range[0], short_range[1] + 1, step):
            for long in range(max(short + step, long_range[0]),
                             long_range[1] + 1, step):
                try:
                    # 生成信号
                    df_signal = self.ma_cross_strategy(df, short, long)

                    # 回测
                    backtest_result = self.backtest_signal(df_signal, 'MA_CROSS', hold_days=20)

                    if backtest_result.empty:
                        continue

                    # 评估
                    score = backtest_result['profit_pct'].mean()

                    if score > best_score:
                        best_score = score
                        best_result = backtest_result
                        best_params = {'short': short, 'long': long}
                        print(f"  发现更优参数: MA{short}/MA{long}, 平均收益: {score:.2f}%")

                except Exception as e:
                    continue

        print(f"\n最优均线参数: MA{best_params['short']}/MA{best_params['long']}")
        print(f"最优收益: {best_score:.2f}%")

        return {
            'params': best_params,
            'result': best_result,
            'score': best_score
        }

    def optimize_rsi_params(self, df: pd.DataFrame,
                           oversold_range: Tuple[int, int] = (20, 40),
                           overbought_range: Tuple[int, int] = (60, 80),
                           step: int = 5) -> Dict:
        """
        优化RSI参数

        Args:
            df: 价格数据
            oversold_range: 超卖阈值范围
            overbought_range: 超买阈值范围
            step: 步长

        Returns:
            最优参数和结果
        """
        best_result = None
        best_score = -float('inf')
        best_params = {}

        print("开始优化RSI参数...")

        for oversold in range(oversold_range[0], oversold_range[1] + 1, step):
            for overbought in range(overbought_range[0], overbought_range[1] + 1, step):
                if oversold >= overbought:
                    continue

                try:
                    # 生成信号
                    df_signal = self.rsi_strategy(df, oversold, overbought)

                    # 回测
                    backtest_result = self.backtest_signal(df_signal, 'RSI_BUY', hold_days=20)

                    if backtest_result.empty:
                        continue

                    # 评估
                    score = backtest_result['profit_pct'].mean()

                    if score > best_score:
                        best_score = score
                        best_result = backtest_result
                        best_params = {'oversold': oversold, 'overbought': overbought}
                        print(f"  发现更优参数: RSI({oversold}, {overbought}), 平均收益: {score:.2f}%")

                except Exception as e:
                    continue

        print(f"\n最优RSI参数: ({best_params['oversold']}, {best_params['overbought']})")
        print(f"最优收益: {best_score:.2f}%")

        return {
            'params': best_params,
            'result': best_result,
            'score': best_score
        }

    # ==================== 综合策略 ====================

    def multi_signal_strategy(self, df: pd.DataFrame,
                             strategies: List[str] = ['MA_CROSS', 'RSI_BUY', 'MACD_GOLDEN'],
                             min_signals: int = 2) -> pd.DataFrame:
        """
        多信号共振策略

        Args:
            df: 价格数据
            strategies: 策略列表
            min_signals: 最少信号数量

        Returns:
            包含综合信号的DataFrame
        """
        result = df.copy()

        # 计算所有策略信号
        if 'MA_CROSS' in strategies:
            result = self.ma_cross_strategy(result, 5, 20)
        if 'RSI_BUY' in strategies or 'RSI_SELL' in strategies:
            result = self.rsi_strategy(result, 30, 70)
        if 'MACD_GOLDEN' in strategies or 'MACD_DEAD' in strategies:
            result = self.macd_strategy(result)
        if 'BB_OVERSOLD' in strategies:
            result = self.bollinger_strategy(result)
        if 'KDJ_BUY' in strategies or 'KDJ_GOLDEN' in strategies:
            result = self.kdj_strategy(result, 20, 80)

        # 计算综合信号
        signal_cols = [col for col in strategies if col in result.columns]
        result['MULTI_SIGNAL'] = result[signal_cols].sum(axis=1)
        result['BUY'] = (result['MULTI_SIGNAL'] >= min_signals).astype(int)

        return result


if __name__ == '__main__':
    optimizer = StrategyOptimizer()
    print("正在获取测试数据...")
    try:
        code = '000001'
        hist_data = optimizer.get_stock_data(code, days=180)

        if hist_data.empty:
            print("获取数据失败，请检查网络或数据源配置")
        else:
            print(f"成功获取 {code} 的 {len(hist_data)} 条数据")
            signal = optimizer.check_limit_up_signal(hist_data, lookback_days=10)
            print(f"涨停策略信号: {'有' if signal else '无'}")
            if signal:
                print(f"  涨停日期: {signal['limit_date']}")
                print(f"  涨停涨幅: {signal['limit_pct']:.2f}%")
    except Exception as e:
        print(f"测试失败: {e}")
