"""Regression guards for RSI edge cases and board-aware limit-up thresholds."""

import numpy as np
import pandas as pd

from quantpy.midterm_portfolio_advisor import _rsi, _rsi_series
from quantpy.qstock_strategy_optimizer import StrategyOptimizer


def _ohlc_from_close(closes) -> pd.DataFrame:
    close = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
        }
    )


def test_calculate_rsi_flat_is_50_not_nan():
    opt = StrategyOptimizer()
    df = _ohlc_from_close(np.full(30, 10.0))
    out = opt.calculate_rsi(df, period=14)
    last = out["RSI"].iloc[-1]
    assert pd.notna(last)
    assert abs(float(last) - 50.0) < 1e-9


def test_calculate_rsi_monotonic_up_is_100():
    opt = StrategyOptimizer()
    df = _ohlc_from_close(np.arange(1.0, 31.0))
    out = opt.calculate_rsi(df, period=14)
    last = out["RSI"].iloc[-1]
    assert pd.notna(last)
    assert abs(float(last) - 100.0) < 1e-9


def test_rsi_helpers_flat_and_up():
    flat = pd.Series(np.full(30, 10.0))
    assert abs(_rsi(flat) - 50.0) < 1e-9
    assert abs(float(_rsi_series(flat).iloc[-1]) - 50.0) < 1e-9

    up = pd.Series(np.arange(1.0, 31.0))
    assert abs(_rsi(up) - 100.0) < 1e-9
    assert abs(float(_rsi_series(up).iloc[-1]) - 100.0) < 1e-9


def test_limit_up_pct_threshold_boards():
    assert StrategyOptimizer.limit_up_pct_threshold("600519") == 9.8
    assert StrategyOptimizer.limit_up_pct_threshold("000001") == 9.8
    assert StrategyOptimizer.limit_up_pct_threshold("300001") == 19.5
    assert StrategyOptimizer.limit_up_pct_threshold("301001") == 19.5
    assert StrategyOptimizer.limit_up_pct_threshold("688001") == 19.5
    assert StrategyOptimizer.limit_up_pct_threshold("689001") == 19.5


def test_limit_up_strategy_chinext_10pct_not_limit():
    """创业板涨 ~10% 不得被 9.8% 主板阈值误判为涨停。"""
    opt = StrategyOptimizer()
    # day0=10, day1=+10% → 11.0
    closes = [10.0] * 12 + [11.0]
    opens = [10.0] * 12 + [10.0]
    df = pd.DataFrame(
        {
            "open": opens,
            "high": closes,
            "low": opens,
            "close": closes,
        }
    )
    main = opt.limit_up_strategy(df, lookback_days=10, code="600000")
    chi = opt.limit_up_strategy(df, lookback_days=10, code="300001")
    assert int(main["is_limit_up"].iloc[-1]) == 1
    assert int(chi["is_limit_up"].iloc[-1]) == 0


def test_limit_up_strategy_chinext_20pct_is_limit():
    opt = StrategyOptimizer()
    closes = [10.0] * 12 + [12.0]  # +20%
    opens = [10.0] * 12 + [10.0]
    df = pd.DataFrame(
        {
            "open": opens,
            "high": closes,
            "low": opens,
            "close": closes,
        }
    )
    chi = opt.limit_up_strategy(df, lookback_days=10, code="300001")
    assert int(chi["is_limit_up"].iloc[-1]) == 1
    assert int(chi["LIMIT_UP_SIGNAL"].iloc[-1]) == 1
