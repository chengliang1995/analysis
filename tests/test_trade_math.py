"""Closed-form checks for trade_math (costs, hold days, missing quotes)."""

from quantpy.trade_math import (
    COMMISSION_RATE,
    STAMP_TAX_RATE,
    hold_trading_days,
    mark_position,
    realized_cash_pnl,
)


def test_mark_position_missing_quote_does_not_wipeout():
    marked = mark_position(10.0, 100, None)
    assert marked["quote_ok"] is False
    assert marked["current_price"] is None
    assert marked["profit_amount"] is None
    assert marked["market_value"] == 1000.0  # cost fallback


def test_mark_position_ok():
    marked = mark_position(10.0, 100, 11.0)
    assert marked["quote_ok"] is True
    assert marked["profit_amount"] == 100.0
    assert marked["profit_pct"] == 10.0


def test_realized_cash_pnl_fees():
    # buy 10 * 100, sell 10 * 100, no price move → fee drag only
    amt, pct, buy_cost, proceeds = realized_cash_pnl(10.0, 10.0, 100)
    assert buy_cost == 10.0 * 100 * (1 + COMMISSION_RATE)
    assert proceeds == 10.0 * 100 * (1 - COMMISSION_RATE - STAMP_TAX_RATE)
    assert amt == round(proceeds - buy_cost, 2)
    assert amt < 0
    assert pct < 0


def test_realized_with_slippage_matches_sim_style():
    # scan 10 → buy exec 10.01, sell trigger 10 → exec 9.99
    slip = 0.001
    amt, pct, buy_cost, proceeds = realized_cash_pnl(
        10.0,
        10.0,
        100,
        buy_slippage_rate=slip,
        sell_slippage_rate=slip,
    )
    buy_exec = 10.0 * (1 + slip)
    sell_exec = 10.0 * (1 - slip)
    expected_buy = buy_exec * 100 * (1 + COMMISSION_RATE)
    expected_proc = sell_exec * 100 * (1 - COMMISSION_RATE - STAMP_TAX_RATE)
    assert abs(buy_cost - expected_buy) < 1e-9
    assert abs(proceeds - expected_proc) < 1e-9
    assert amt == round(expected_proc - expected_buy, 2)


def test_hold_trading_days_same_day():
    assert hold_trading_days("2024-01-02", "2024-01-02") == 0


def test_hold_trading_days_weekend_span_uses_business_fallback_or_cal():
    # Fri to Mon: 1 trading day interval if calendar works; at least not 3 calendar days
    days = hold_trading_days("2024-01-05", "2024-01-08")  # Fri→Mon
    assert days <= 1
