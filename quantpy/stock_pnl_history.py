"""按股票代码汇总实盘历史盈亏（清盘记录 + 交易日记，去重）。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from quantpy.portfolio import PortfolioManager, classify_bucket
from quantpy.trade_journal import TradeJournal


def _hold_days(buy_date: str, sell_date: str) -> int:
    try:
        buy = datetime.strptime(str(buy_date)[:10], "%Y-%m-%d")
        sell = datetime.strptime(str(sell_date)[:10], "%Y-%m-%d")
        return max((sell - buy).days, 0)
    except ValueError:
        return 0


def _trade_key(row: dict) -> tuple:
    return (
        str(row.get("code", "")).zfill(6),
        str(row.get("sell_date", ""))[:10],
        int(row.get("quantity", 0)),
        round(float(row.get("sell_price", 0)), 4),
    )


def _row_from_closed(c: dict) -> dict:
    buy_p = float(c.get("cost_price", 0))
    sell_p = float(c.get("sell_price", 0))
    qty = int(c.get("quantity", 0))
    profit_amount = float(c.get("profit_amount", (sell_p - buy_p) * qty))
    profit_pct = float(
        c.get("profit_pct", (sell_p - buy_p) / buy_p * 100 if buy_p else 0)
    )
    return {
        "code": str(c.get("code", "")).zfill(6),
        "name": c.get("name", ""),
        "buy_date": str(c.get("buy_date", ""))[:10],
        "sell_date": str(c.get("sell_date", ""))[:10],
        "buy_price": round(buy_p, 4),
        "sell_price": round(sell_p, 4),
        "quantity": qty,
        "strategy": c.get("strategy", "手动"),
        "bucket": c.get("bucket", classify_bucket(c.get("strategy", ""))),
        "profit_pct": round(profit_pct, 2),
        "profit_amount": round(profit_amount, 2),
        "hold_days": _hold_days(c.get("buy_date", ""), c.get("sell_date", "")),
        "source": "portfolio",
    }


def _summarize_trades(trades: List[dict]) -> dict:
    if not trades:
        return {
            "trade_count": 0,
            "win_count": 0,
            "win_rate": 0.0,
            "total_profit_amount": 0.0,
            "avg_profit_pct": 0.0,
        }
    wins = [t for t in trades if t["profit_amount"] > 0]
    total_amt = sum(t["profit_amount"] for t in trades)
    return {
        "trade_count": len(trades),
        "win_count": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_profit_amount": round(total_amt, 2),
        "avg_profit_pct": round(sum(t["profit_pct"] for t in trades) / len(trades), 2),
    }


def collect_all_realized_trades(days: Optional[int] = None) -> List[dict]:
    """合并 portfolio 清盘与交易日记，按 sell_date+code+数量+卖价去重。"""
    pm = PortfolioManager()
    rows: List[dict] = []
    seen: set[tuple] = set()

    for c in pm._portfolio.closed_positions:
        item = _row_from_closed(c.to_dict() if hasattr(c, "to_dict") else dict(c))
        key = _trade_key(item)
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)

    journal = TradeJournal()
    jdf = journal.list_trades(days=days)
    if not jdf.empty:
        for _, r in jdf.iterrows():
            item = {
                "code": str(r.get("code", "")).zfill(6),
                "name": r.get("name", ""),
                "buy_date": str(r.get("buy_date", ""))[:10],
                "sell_date": str(r.get("sell_date", ""))[:10],
                "buy_price": round(float(r.get("buy_price", 0)), 4),
                "sell_price": round(float(r.get("sell_price", 0)), 4),
                "quantity": int(r.get("quantity", 0)),
                "strategy": r.get("strategy", "手动"),
                "bucket": classify_bucket(r.get("strategy", "")),
                "profit_pct": round(float(r.get("profit_pct", 0)), 2),
                "profit_amount": round(float(r.get("profit_amount", 0)), 2),
                "hold_days": int(r.get("hold_days", 0)),
                "source": "journal",
            }
            key = _trade_key(item)
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)

    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        rows = [r for r in rows if r.get("sell_date", "") >= cutoff]

    rows.sort(key=lambda x: x.get("sell_date", ""), reverse=True)
    return rows


def build_pnl_summary_by_code(days: Optional[int] = None) -> Dict[str, dict]:
    """各代码历史盈亏汇总（供持仓表展示）。"""
    by_code: Dict[str, List[dict]] = {}
    for item in collect_all_realized_trades(days=days):
        code = item["code"]
        by_code.setdefault(code, []).append(item)

    out: Dict[str, dict] = {}
    for code, trades in by_code.items():
        summary = _summarize_trades(trades)
        summary["name"] = trades[0].get("name", code)
        out[code] = summary
    return out


def get_stock_pnl_history(
    code: str,
    days: Optional[int] = None,
    include_current: bool = True,
) -> dict:
    """单只股票历史盈亏明细 + 汇总；可选附带当前持仓浮盈。"""
    code = str(code).zfill(6)
    trades = [t for t in collect_all_realized_trades(days=days) if t["code"] == code]
    summary = _summarize_trades(trades)
    name = trades[0]["name"] if trades else ""

    current = None
    if include_current:
        pm = PortfolioManager()
        for pos in pm._portfolio.positions:
            if str(pos.code).zfill(6) != code:
                continue
            name = name or pos.name
            from quantpy.stock_data import get_latest_price

            price = get_latest_price(code)
            cost = pos.cost_price * pos.quantity
            mv = price * pos.quantity if price > 0 else cost
            profit_amount = mv - cost
            profit_pct = (price - pos.cost_price) / pos.cost_price * 100 if pos.cost_price > 0 else 0
            current = {
                "quantity": pos.quantity,
                "cost_price": pos.cost_price,
                "current_price": round(price, 2) if price > 0 else None,
                "profit_amount": round(profit_amount, 2),
                "profit_pct": round(profit_pct, 2),
                "strategy": pos.strategy,
                "bucket": classify_bucket(pos.strategy),
                "buy_date": pos.buy_date,
            }
            break

    return {
        "code": code,
        "name": name,
        "has_data": bool(trades) or current is not None,
        **summary,
        "trades": trades,
        "current_position": current,
    }
