"""Shared trade math: costs, hold days, quote-safe mark-to-market.

Rates are decimals (0.00025), not percents. Do not fill missing prices with 0
when computing P&L — missing stays missing.
"""

from __future__ import annotations

from typing import Optional, Tuple

# A-share retail defaults (match SimConfig itemized fees; no slippage on real fills)
COMMISSION_RATE = 0.00025
STAMP_TAX_RATE = 0.0005


def hold_trading_days(buy_date: str, sell_date: str) -> int:
    """Trading days between buy and sell (exclusive of buy if same calendar span).

    Returns max(len(calendar) - 1, 0). Falls back to business-day count, then
    calendar days, when the exchange calendar cannot be loaded.

    Live sessions often lack today's unfinished bar in hist calendars; if
    ``sell_date`` is today (weekday) and missing from the calendar, append it
    so hold/expiry exits are not stuck at 0.
    """
    buy_d = str(buy_date or "")[:10]
    sell_d = str(sell_date or "")[:10]
    if not buy_d or not sell_d:
        return 0
    cal: list = []
    try:
        from quantpy.midterm_pick_tracker import _trading_days_between

        cal = list(_trading_days_between(buy_d, sell_d) or [])
    except Exception:
        cal = []
    if not cal:
        try:
            import pandas as pd

            buy = pd.Timestamp(buy_d)
            sell = pd.Timestamp(sell_d)
            return max(int(len(pd.bdate_range(buy, sell)) - 1), 0)
        except (ValueError, TypeError):
            pass
        try:
            from datetime import datetime as _dt

            return max((_dt.strptime(sell_d, "%Y-%m-%d") - _dt.strptime(buy_d, "%Y-%m-%d")).days, 0)
        except ValueError:
            return 0

    # 盘中 hist 常缺当日未收盘 bar → 持仓天数少计 1，到期/持仓纪律失效
    if sell_d not in cal:
        try:
            from datetime import datetime as _dt

            sell_ts = _dt.strptime(sell_d, "%Y-%m-%d")
            today = _dt.now().strftime("%Y-%m-%d")
            if sell_d == today and sell_ts.weekday() < 5:
                if not cal or sell_d > cal[-1]:
                    cal = list(cal) + [sell_d]
        except ValueError:
            pass

    return max(len(cal) - 1, 0)


def realized_cash_pnl(
    buy_price: float,
    sell_price: float,
    quantity: int,
    *,
    commission_rate: float = COMMISSION_RATE,
    stamp_tax_rate: float = STAMP_TAX_RATE,
    buy_slippage_rate: float = 0.0,
    sell_slippage_rate: float = 0.0,
) -> Tuple[float, float, float, float]:
    """Cash P&L after fees.

    Returns (profit_amount, profit_pct, buy_cost, proceeds).
    profit_pct is vs buy_cost (cash outlay including buy commission).
    Buy/sell prices are intended fill prices before applying slippage rates.
    """
    qty = int(quantity)
    buy_px = float(buy_price) * (1.0 + float(buy_slippage_rate))
    sell_px = float(sell_price) * (1.0 - float(sell_slippage_rate))
    buy_cost = buy_px * qty * (1.0 + float(commission_rate))
    proceeds = sell_px * qty * (1.0 - float(commission_rate) - float(stamp_tax_rate))
    profit_amount = round(proceeds - buy_cost, 2)
    profit_pct = round(profit_amount / buy_cost * 100, 2) if buy_cost > 0 else 0.0
    return profit_amount, profit_pct, buy_cost, proceeds


def mark_position(
    cost_price: float,
    quantity: int,
    market_price: Optional[float],
) -> dict:
    """Mark-to-market without inventing a wipeout when the quote is missing.

    Missing/non-positive price → current_price None, float P&L None, market_value
    falls back to cost so portfolio equity is not falsely crushed.
    """
    qty = int(quantity)
    cost = float(cost_price)
    cost_amount = cost * qty
    px = float(market_price) if market_price is not None else 0.0
    if px > 0 and cost > 0:
        market_value = px * qty
        return {
            "quote_ok": True,
            "current_price": round(px, 2),
            "cost_amount": round(cost_amount, 2),
            "market_value": round(market_value, 2),
            "profit_amount": round(market_value - cost_amount, 2),
            "profit_pct": round((px - cost) / cost * 100, 2),
        }
    return {
        "quote_ok": False,
        "current_price": None,
        "cost_amount": round(cost_amount, 2),
        "market_value": round(cost_amount, 2),
        "profit_amount": None,
        "profit_pct": None,
    }
