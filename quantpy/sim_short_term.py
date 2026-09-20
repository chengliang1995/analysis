"""模拟盘短线强势子账户（独立 20 万）。

strategy_id = short_term_picker；state key = ``short_term``。
与超短主账户 / 三倍量 / MA20 / Serenity 隔离。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from quantpy.short_term_picker import STRATEGY_ID, run_short_term_pick
from quantpy.sim_midterm import (
    _calc_midterm_quantity,
    _hold_days,
    _is_sellable,
    _norm_date,
    _progress,
    _sim_held_codes,
    _today,
)
from quantpy.stock_data import get_realtime_quotes, get_stock_hist
from quantpy.trade_math import mark_position

if TYPE_CHECKING:
    import pandas as pd

    from quantpy.sim_replay import SimReplayEngine

DEFAULT_SHORT_TERM_CAPITAL = 200_000.0
STATE_KEY = "short_term"


@dataclass
class ShortTermSimConfig:
    capital: float = DEFAULT_SHORT_TERM_CAPITAL
    max_positions: int = 5
    max_single_weight_pct: float = 25.0
    stop_loss_pct: float = -8.0
    take_profit_pct: float = 15.0
    max_hold_days: int = 10
    min_score: float = 55.0
    max_new_per_run: int = 2
    t_plus_one: bool = True
    # 跌破均线多头则离场
    exit_on_ma_break: bool = True


def default_short_term_state() -> dict:
    cfg = ShortTermSimConfig()
    return {
        "config": asdict(cfg),
        "initial_capital": cfg.capital,
        "cash": cfg.capital,
        "positions": [],
        "closed_trades": [],
        "pick_log": [],
        "last_scan": [],
        "last_record_date": "",
        "last_buy_date": "",
        "updated_at": "",
        "strategy": STRATEGY_ID,
    }


def ensure_short_term_state(state: dict) -> dict:
    if STATE_KEY not in state or not isinstance(state.get(STATE_KEY), dict):
        state[STATE_KEY] = default_short_term_state()
        return state[STATE_KEY]
    mt = state[STATE_KEY]
    defaults = default_short_term_state()
    for key, val in defaults.items():
        if key not in mt:
            mt[key] = val
    if "config" not in mt:
        mt["config"] = defaults["config"]
    cfg = mt.get("config") or {}
    if "capital" not in cfg:
        cfg["capital"] = DEFAULT_SHORT_TERM_CAPITAL
        mt["config"] = cfg
    return mt


def _cfg(mt: dict) -> ShortTermSimConfig:
    return ShortTermSimConfig(**{**asdict(ShortTermSimConfig()), **(mt.get("config") or {})})


def _ma_still_bull(code: str) -> bool:
    """价≥MA5>MA10>MA20 仍成立。"""
    try:
        hist = get_stock_hist(code, days=60, patch_live=False)
    except Exception:
        return True
    if hist is None or hist.empty or "close" not in hist.columns or len(hist) < 25:
        return True
    close = hist["close"].astype(float)
    price = float(close.iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    return bool(price >= ma5 and ma5 > ma10 > ma20)


def _held(engine: "SimReplayEngine") -> set[str]:
    mt = ensure_short_term_state(engine.state)
    held = {str(p["code"]).zfill(6) for p in mt.get("positions", [])}
    held |= _sim_held_codes(engine)
    return held


def check_short_term_sim_exits(
    engine: "SimReplayEngine",
    *,
    show_progress: bool = False,
) -> List[dict]:
    mt = ensure_short_term_state(engine.state)
    positions = mt.get("positions", [])
    if not positions:
        return []

    cfg = _cfg(mt)
    quotes = get_realtime_quotes([p["code"] for p in positions])
    if quotes.empty:
        return []

    qmap = quotes.set_index("code")
    today = _today()
    closed: List[dict] = []
    remain = []

    for p in positions:
        code = str(p["code"]).zfill(6)
        if code not in qmap.index:
            remain.append(p)
            continue
        q = qmap.loc[code]
        price = float(q["close"])
        low = float(q.get("low", price))
        buy_date = _norm_date(p["buy_date"])
        hold = _hold_days(buy_date, today)
        qty = int(p["quantity"])
        buy_price = float(p["buy_price"])

        if not _is_sellable(buy_date, cfg, today):
            remain.append(p)
            continue

        sell_price = None
        reason = ""
        stop = float(p.get("stop_loss") or buy_price * (1 + cfg.stop_loss_pct / 100))
        take = float(p.get("take_profit") or buy_price * (1 + cfg.take_profit_pct / 100))

        if low <= stop:
            sell_price = stop
            reason = f"止损({cfg.stop_loss_pct}%)"
        elif price >= take:
            sell_price = price
            reason = f"止盈({cfg.take_profit_pct}%)"
        elif hold >= cfg.max_hold_days:
            sell_price = price
            reason = f"持仓{cfg.max_hold_days}交易日到期"
        elif cfg.exit_on_ma_break and not _ma_still_bull(code):
            sell_price = price
            reason = "均线多头破坏离场"

        if sell_price is None:
            remain.append(p)
            continue

        profit_amount = (sell_price - buy_price) * qty
        trade = {
            "code": code,
            "name": p["name"],
            "buy_date": p["buy_date"],
            "buy_price": buy_price,
            "sell_date": today,
            "sell_price": round(float(sell_price), 2),
            "quantity": qty,
            "profit_pct": round((sell_price - buy_price) / buy_price * 100, 2),
            "profit_amount": round(profit_amount, 2),
            "hold_days": hold,
            "exit_reason": reason,
            "score": p.get("score", 0),
            "strategy": STRATEGY_ID,
        }
        closed.append(trade)
        mt["cash"] = round(float(mt.get("cash", 0)) + sell_price * qty, 2)
        _progress(
            f"  [短线模拟] 卖出 {p['name']}({code}) @{sell_price:.2f} {reason}",
            show_progress,
        )

    mt["positions"] = remain
    if closed:
        mt.setdefault("closed_trades", []).extend(closed)
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()
    return closed


def run_short_term_sim_buy(
    engine: "SimReplayEngine",
    recommendations: List[dict],
    *,
    show_progress: bool = False,
    force: bool = False,
) -> dict:
    mt = ensure_short_term_state(engine.state)
    cfg = _cfg(mt)
    today = _today()
    positions = list(mt.get("positions", []))
    held = {str(p["code"]).zfill(6) for p in positions}
    cash = float(mt.get("cash", cfg.capital))
    bought: List[dict] = []
    skipped: List[dict] = []

    if not force and mt.get("last_buy_date") == today and len(positions) >= cfg.max_positions:
        return {"bought": [], "skipped": [{"reason": "今日已买满"}], "cash": cash}

    recs = sorted(recommendations, key=lambda x: float(x.get("score") or 0), reverse=True)
    new_count = 0
    for rec in recs:
        if new_count >= cfg.max_new_per_run:
            break
        if len(positions) >= cfg.max_positions:
            skipped.append({"code": rec.get("code"), "reason": "仓位已满"})
            break
        code = str(rec.get("code", "")).zfill(6)
        if code in held:
            skipped.append({"code": code, "reason": "已有仓不加仓"})
            continue
        score = float(rec.get("score") or 0)
        if score < cfg.min_score:
            skipped.append({"code": code, "reason": f"评分{score:.0f}<{cfg.min_score}"})
            continue
        buy_price = float(rec.get("price") or 0)
        if buy_price <= 0:
            skipped.append({"code": code, "reason": "无有效价格"})
            continue
        slots_left = cfg.max_positions - len(positions) - len(bought)
        qty = _calc_midterm_quantity(
            buy_price, cash, cfg.capital, cfg.max_single_weight_pct, slots_left,
        )
        if qty <= 0:
            skipped.append({"code": code, "reason": "资金不足"})
            break
        cost = buy_price * qty
        if cost > cash:
            skipped.append({"code": code, "reason": "现金不足"})
            continue

        pos = {
            "code": code,
            "name": str(rec.get("name", code)),
            "quantity": qty,
            "buy_price": round(buy_price, 2),
            "buy_date": today,
            "stop_loss": round(buy_price * (1 + cfg.stop_loss_pct / 100), 2),
            "take_profit": round(buy_price * (1 + cfg.take_profit_pct / 100), 2),
            "score": score,
            "limit_up_count_20d": rec.get("limit_up_count_20d"),
            "vol_ratio_5d": rec.get("vol_ratio_5d"),
            "reason": "短线强势·涨停基因+MA多头",
            "board": rec.get("board"),
            "strategy": STRATEGY_ID,
            "id": uuid.uuid4().hex[:10],
        }
        positions.append(pos)
        cash -= cost
        held.add(code)
        bought.append(pos)
        new_count += 1
        _progress(
            f"  [短线模拟] 买入 {pos['name']}({code}) {qty}股 @{buy_price:.2f} 分{score:.0f}",
            show_progress,
        )

    mt["positions"] = positions
    mt["cash"] = round(cash, 2)
    if bought:
        mt["last_buy_date"] = today
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()
    return {"bought": bought, "skipped": skipped, "cash": mt["cash"], "position_count": len(positions)}


def record_short_term_picks(
    engine: "SimReplayEngine",
    recommendations: List[dict],
    *,
    show_progress: bool = False,
) -> List[dict]:
    mt = ensure_short_term_state(engine.state)
    today = _today()
    logged = []
    for rec in recommendations:
        entry = {
            "date": today,
            "code": str(rec.get("code", "")).zfill(6),
            "name": rec.get("name", ""),
            "score": rec.get("score"),
            "price": rec.get("price"),
            "limit_up_count_20d": rec.get("limit_up_count_20d"),
            "market_cap_yi": rec.get("market_cap_yi"),
            "vol_ratio_5d": rec.get("vol_ratio_5d"),
            "board": rec.get("board"),
            "strategy": STRATEGY_ID,
        }
        logged.append(entry)
    if logged:
        mt.setdefault("pick_log", [])
        mt["pick_log"] = logged + list(mt.get("pick_log") or [])
        mt["pick_log"] = mt["pick_log"][:100]
        mt["last_record_date"] = today
        mt["last_scan"] = [
            {
                "code": e["code"],
                "name": e["name"],
                "score": e["score"],
                "price": e["price"],
                "limit_up_count_20d": e.get("limit_up_count_20d"),
                "market_cap_yi": e.get("market_cap_yi"),
                "vol_ratio_5d": e.get("vol_ratio_5d"),
                "board": e.get("board"),
            }
            for e in logged
        ]
        mt["updated_at"] = datetime.now().isoformat()
        engine._save_state()
        _progress(f"  [短线模拟] 记录选股 {len(logged)} 只", show_progress)
    return logged


def run_sim_short_term_select(
    engine: "SimReplayEngine",
    *,
    show_progress: bool = False,
    force: bool = False,
    max_candidates: int = 30,
    max_analyze: int = 400,
) -> dict:
    """短线强势模拟选股 + 自动建仓（20 万账户）。"""
    try:
        ensure_short_term_state(engine.state)
        _progress("=" * 50, show_progress)
        _progress("模拟短线强势选股 · 20万 · 涨停基因+MA多头", show_progress)

        held = _held(engine)
        scan = run_short_term_pick(
            max_candidates=max_candidates,
            max_analyze=max_analyze,
            show_progress=show_progress,
        )
        if not scan.get("ok"):
            return {
                "ok": False,
                "message": scan.get("message") or "短线筛选失败",
                "summary": enrich_short_term_sim(engine.state),
            }

        recs = [
            c for c in (scan.get("candidates") or [])
            if str(c.get("code") or "").zfill(6) not in held
        ]
        closed = check_short_term_sim_exits(engine, show_progress=show_progress)
        logged = record_short_term_picks(engine, recs, show_progress=show_progress)
        buy_result = run_short_term_sim_buy(
            engine, recs, show_progress=show_progress, force=force,
        )
        buy_n = len(buy_result.get("bought", []))
        message = (
            f"短线强势命中 {len(recs)} 只，买入 {buy_n} 只"
            if recs
            else "今日暂无短线强势合格候选"
        )
        _progress(f"  完成：{message}", show_progress)
        return {
            "ok": True,
            "message": message,
            "strategy": STRATEGY_ID,
            "candidates": recs,
            "closed_today": closed,
            "pick_logged": len(logged),
            "bought": buy_result.get("bought", []),
            "skipped": buy_result.get("skipped", []),
            "summary": enrich_short_term_sim(engine.state),
            "hit_count": len(recs),
            "scan": scan,
        }
    except Exception as exc:
        import traceback

        err = traceback.format_exc()
        _progress(f"  [失败] {exc}", show_progress)
        return {
            "ok": False,
            "message": f"短线模拟选股失败: {exc}",
            "error": err,
            "summary": enrich_short_term_sim(engine.state),
        }


def enrich_short_term_sim(state: dict, quotes_df: Optional["pd.DataFrame"] = None) -> dict:
    mt = ensure_short_term_state(state)
    cfg = _cfg(mt)
    positions = list(mt.get("positions", []))

    if quotes_df is not None and not quotes_df.empty:
        qmap = quotes_df.copy()
        qmap["code"] = qmap["code"].astype(str).str.zfill(6)
        qmap = qmap.set_index("code")
    elif positions:
        quotes = get_realtime_quotes([p["code"] for p in positions])
        qmap = quotes.set_index("code") if quotes is not None and not quotes.empty else None
    else:
        qmap = None

    today = _today()
    enriched = []
    total_mv = 0.0
    quote_missing = 0
    capital = float(mt.get("initial_capital", cfg.capital))

    for p in positions:
        code = str(p["code"]).zfill(6)
        raw = float(qmap.loc[code, "close"]) if qmap is not None and code in qmap.index else 0.0
        marked = mark_position(p["buy_price"], p["quantity"], raw if raw > 0 else None)
        if not marked["quote_ok"]:
            quote_missing += 1
        total_mv += marked["market_value"]
        enriched.append({
            **p,
            "current_price": marked["current_price"],
            "market_value": marked["market_value"],
            "profit_amount": marked["profit_amount"],
            "profit_pct": marked["profit_pct"],
            "quote_ok": marked["quote_ok"],
            "weight_pct": round(marked["market_value"] / capital * 100, 2) if capital else 0,
            "sellable_today": _is_sellable(p["buy_date"], cfg, today),
            "t_plus_one_locked": cfg.t_plus_one and not _is_sellable(p["buy_date"], cfg, today),
        })

    cash = float(mt.get("cash", cfg.capital))
    equity = cash + total_mv
    initial = float(mt.get("initial_capital", cfg.capital))
    closed = list(mt.get("closed_trades", []))
    closed.sort(key=lambda x: x.get("sell_date", ""), reverse=True)

    return {
        "has_data": True,
        "initial_capital": initial,
        "cash": round(cash, 2),
        "market_value": round(total_mv, 2),
        "equity": round(equity, 2),
        "total_return_pct": round((equity - initial) / initial * 100, 2) if initial else 0,
        "position_count": len(enriched),
        "quote_missing_count": quote_missing,
        "closed_count": len(closed),
        "positions": enriched,
        "closed_trades": closed[:10],
        "pick_log": list(mt.get("pick_log") or [])[:20],
        "last_scan": list(mt.get("last_scan") or [])[:20],
        "config": asdict(cfg),
        "strategy": STRATEGY_ID,
        "updated_at": mt.get("updated_at", ""),
    }
