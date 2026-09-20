"""
模拟盘中线账户（15 万额度）
- 选股策略：三倍量观察池（缩量站稳 MA5 买点），不扫全市场突破
- 记录选股结果、按观察池买点自动模拟建仓、中线持仓复盘
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

import pandas as pd

from quantpy.stock_data import get_realtime_quotes

if TYPE_CHECKING:
    from quantpy.sim_replay import SimReplayEngine

DEFAULT_MIDTERM_CAPITAL = 150_000.0


@dataclass
class MidtermSimConfig:
    capital: float = DEFAULT_MIDTERM_CAPITAL
    max_positions: int = 5
    max_single_weight_pct: float = 30.0
    stop_loss_pct: float = -8.0
    take_profit_pct: float = 15.0
    max_hold_days: int = 30
    min_score: int = 60
    max_new_per_run: int = 2
    t_plus_one: bool = True


@dataclass
class MidtermSimPosition:
    code: str
    name: str
    quantity: int
    buy_price: float
    buy_date: str
    stop_loss: float
    take_profit: float
    midterm_score: float = 0.0
    reason: str = ""
    tags: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


def default_midterm_state() -> dict:
    cfg = MidtermSimConfig()
    return {
        "config": asdict(cfg),
        "initial_capital": cfg.capital,
        "cash": cfg.capital,
        "positions": [],
        "closed_trades": [],
        "pick_log": [],
        "last_record_date": "",
        "last_buy_date": "",
        "last_reviews": [],
        "updated_at": "",
    }


def ensure_midterm_state(state: dict) -> dict:
    """确保 sim_state 含 midterm 子账户（兼容旧文件）。"""
    if "midterm" not in state or not isinstance(state.get("midterm"), dict):
        state["midterm"] = default_midterm_state()
        return state["midterm"]
    mt = state["midterm"]
    defaults = default_midterm_state()
    for key, val in defaults.items():
        if key not in mt:
            mt[key] = val
    if "config" not in mt:
        mt["config"] = defaults["config"]
    # 旧版 last_pick_date 仅表示记录选股，迁移为 last_record_date，不再阻断买入
    if mt.get("last_pick_date") and not mt.get("last_record_date"):
        mt["last_record_date"] = mt["last_pick_date"]
    return mt


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_date(value: Optional[str]) -> str:
    return str(value or "")[:10]


def _is_sellable(buy_date: str, cfg: MidtermSimConfig, as_of: Optional[str] = None) -> bool:
    if not cfg.t_plus_one:
        return True
    return _norm_date(buy_date) < _norm_date(as_of or _today())


def _hold_days(buy_date: str, as_of: Optional[str] = None) -> int:
    from quantpy.trade_math import hold_trading_days

    return hold_trading_days(buy_date, as_of or _today())


def _calc_midterm_quantity(
    buy_price: float,
    cash: float,
    capital: float,
    max_weight_pct: float,
    slots: int,
) -> int:
    if buy_price <= 0 or slots <= 0 or cash <= 0:
        return 0
    per_slot = cash / slots
    cap_budget = capital * max_weight_pct / 100
    budget = min(per_slot, cap_budget, cash)
    qty = int(budget / buy_price / 100) * 100
    return max(qty, 0)


def _progress(msg: str, show: bool) -> None:
    if show:
        print(msg, flush=True)


def record_midterm_picks(
    engine: SimReplayEngine,
    recommendations: List[dict],
    *,
    show_progress: bool = False,
    source: str = "triple_volume",
) -> List[dict]:
    """将中线推荐写入选股记录（不去重当日同代码）。"""
    mt = ensure_midterm_state(engine.state)
    today = _today()
    logged: List[dict] = []
    held = {str(p["code"]).zfill(6) for p in mt.get("positions", [])}

    for rec in recommendations:
        code = str(rec.get("code", "")).zfill(6)
        if not code or code == "000000":
            continue
        entry = {
            "date": today,
            "code": code,
            "name": rec.get("name", code),
            "price": round(float(rec.get("price") or 0), 2),
            "midterm_score": float(rec.get("midterm_score") or 0),
            "reason": str(rec.get("reason") or "")[:200],
            "tags": str(rec.get("tags") or ""),
            "industry": rec.get("industry") or "",
            "source": source,
            "action": "logged",
            "skip_reason": "",
        }
        if code in held:
            entry["action"] = "skipped"
            entry["skip_reason"] = "已持仓"
        logged.append(entry)

    if logged:
        mt.setdefault("pick_log", [])
        mt["pick_log"].extend(logged)
        mt["pick_log"] = mt["pick_log"][-200:]
        mt["last_record_date"] = today
        mt["updated_at"] = datetime.now().isoformat()
        engine._save_state()
        _progress(f"  [模拟中线] 记录选股 {len(logged)} 只", show_progress)
    return logged


def clear_midterm_sim_selections(
    engine: SimReplayEngine,
    *,
    close_positions: bool = True,
    show_progress: bool = False,
) -> dict:
    """清除模拟中线选股记录；策略切换时可平掉当前持仓。"""
    mt = ensure_midterm_state(engine.state)
    cleared_picks = len(mt.get("pick_log", []))
    mt["pick_log"] = []
    mt["last_record_date"] = ""
    mt["last_reviews"] = []

    closed: List[dict] = []
    if close_positions and mt.get("positions"):
        positions = list(mt["positions"])
        codes = [p["code"] for p in positions]
        quotes = get_realtime_quotes(codes)
        price_map: Dict[str, float] = {}
        if not quotes.empty:
            for _, row in quotes.iterrows():
                price_map[str(row["code"]).zfill(6)] = float(row["close"])
        today = _today()
        for p in positions:
            code = str(p["code"]).zfill(6)
            sell_price = price_map.get(code, float(p["buy_price"]))
            profit_pct = (
                (sell_price - p["buy_price"]) / p["buy_price"] * 100 if p["buy_price"] else 0
            )
            profit_amount = (sell_price - p["buy_price"]) * p["quantity"]
            trade = {
                "code": code,
                "name": p["name"],
                "buy_date": p["buy_date"],
                "buy_price": p["buy_price"],
                "sell_date": today,
                "sell_price": round(sell_price, 2),
                "quantity": p["quantity"],
                "profit_pct": round(profit_pct, 2),
                "profit_amount": round(profit_amount, 2),
                "hold_days": _hold_days(p["buy_date"], today),
                "exit_reason": "策略切换清仓",
                "midterm_score": p.get("midterm_score", 0),
            }
            mt["cash"] = float(mt.get("cash", 0)) + sell_price * p["quantity"]
            closed.append(trade)
            _progress(
                f"  [模拟中线] 清仓 {p['name']}({code}) @{sell_price:.2f} {profit_pct:+.1f}%",
                show_progress,
            )
        mt["positions"] = []

    mt["last_buy_date"] = ""
    mt["updated_at"] = datetime.now().isoformat()
    if closed:
        mt.setdefault("closed_trades", [])
        mt["closed_trades"].extend(closed)
    engine._save_state()
    _progress(
        f"  [模拟中线] 已清除选股 {cleared_picks} 条，平仓 {len(closed)} 只",
        show_progress,
    )
    return {
        "cleared_picks": cleared_picks,
        "closed_positions": closed,
        "summary": enrich_midterm_sim(engine.state),
    }


def _apply_today_buy_prices(
    recommendations: List[dict],
    *,
    ref_date: Optional[str] = None,
) -> tuple[List[dict], List[dict]]:
    """买入价仅用当日行情；拒绝非今日信号价或缺失行情。"""
    ref_date = _norm_date(ref_date or _today())
    codes = [
        str(r.get("code", "")).zfill(6)
        for r in recommendations
        if str(r.get("code", "")).zfill(6) not in ("", "000000")
    ]
    price_map: Dict[str, float] = {}
    if codes:
        quotes = get_realtime_quotes(codes)
        if not quotes.empty:
            for _, row in quotes.iterrows():
                price_map[str(row["code"]).zfill(6)] = float(row["close"])

    out: List[dict] = []
    skipped: List[dict] = []
    for rec in recommendations:
        code = str(rec.get("code", "")).zfill(6)
        if not code or code == "000000":
            skipped.append({"code": code, "reason": "无效代码"})
            continue
        sig_date = _norm_date(rec.get("buy_signal_date") or rec.get("signal_date"))
        if sig_date and sig_date != ref_date:
            skipped.append({"code": code, "reason": f"信号日{sig_date}非买入日{ref_date}"})
            continue
        buy_price = price_map.get(code, 0.0)
        if buy_price <= 0:
            skipped.append({"code": code, "reason": "无当日行情"})
            continue
        updated = dict(rec)
        hist_px = float(rec.get("price") or 0)
        updated["price"] = round(buy_price, 2)
        if hist_px > 0 and abs(hist_px - buy_price) > 0.009:
            updated["tags"] = (
                f"{rec.get('tags', '')},当日价{buy_price:.2f}"
                f"(信号价{hist_px:.2f})"
            ).strip(",")
        out.append(updated)
    return out, skipped


def run_midterm_sim_buy(
    engine: SimReplayEngine,
    recommendations: List[dict],
    *,
    show_progress: bool = False,
    top_n: int = 5,
    force: bool = False,
) -> dict:
    """按中线推荐模拟买入（15 万账户）。"""
    mt = ensure_midterm_state(engine.state)
    cfg = MidtermSimConfig(**{**asdict(MidtermSimConfig()), **mt.get("config", {})})
    today = _today()

    last_buy = mt.get("last_buy_date") or ""
    positions = list(mt.get("positions", []))
    if last_buy == today and not force:
        _progress(
            f"  [模拟中线] 今日已执行建仓，持仓 {len(positions)} 只（强制重选可加 force）",
            show_progress,
        )
        return {"bought": [], "skipped": [], "message": "今日已建仓"}

    positions = list(mt.get("positions", []))
    held = {str(p["code"]).zfill(6) for p in positions}
    cash = float(mt.get("cash", cfg.capital))
    open_slots = cfg.max_positions - len(positions)
    max_new = min(cfg.max_new_per_run, open_slots) if open_slots > 0 else 0

    bought: List[dict] = []
    skipped: List[dict] = []

    if max_new <= 0:
        _progress("  [模拟中线] 持仓已满，仅记录选股", show_progress)
        return {"bought": [], "skipped": [], "message": "持仓已满"}

    priced_recs, price_skipped = _apply_today_buy_prices(recommendations, ref_date=today)
    skipped.extend(price_skipped)

    candidates = sorted(
        priced_recs,
        key=lambda x: float(x.get("midterm_score") or 0),
        reverse=True,
    )[:top_n]

    for rec in candidates:
        if len(bought) >= max_new:
            break
        code = str(rec.get("code", "")).zfill(6)
        if not code or code in held:
            skipped.append({"code": code, "reason": "已持仓或无效"})
            continue
        score = float(rec.get("midterm_score") or 0)
        from quantpy.selection_tuning import build_selection_tuning
        min_score = max(cfg.min_score, build_selection_tuning().triple_min_score)
        if score < min_score:
            skipped.append({"code": code, "reason": f"评分{score:.0f}<{min_score}"})
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

        pos = MidtermSimPosition(
            code=code,
            name=str(rec.get("name", code)),
            quantity=qty,
            buy_price=round(buy_price, 2),
            buy_date=today,
            stop_loss=round(buy_price * (1 + cfg.stop_loss_pct / 100), 2),
            take_profit=round(buy_price * (1 + cfg.take_profit_pct / 100), 2),
            midterm_score=score,
            reason=str(rec.get("reason") or "")[:120],
            tags=str(rec.get("tags") or ""),
        )
        positions.append(asdict(pos))
        cash -= cost
        held.add(code)
        bought.append(asdict(pos))
        _progress(
            f"  [模拟中线] 买入 {pos.name}({code}) {qty}股 @{buy_price:.2f} 评分{score:.0f}",
            show_progress,
        )

        for entry in mt.get("pick_log", []):
            if entry.get("date") == today and entry.get("code") == code:
                entry["action"] = "bought"
                entry["skip_reason"] = ""

    mt["positions"] = positions
    mt["cash"] = round(cash, 2)
    mt["last_buy_date"] = today
    mt.pop("last_pick_date", None)
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()

    if not bought:
        _progress("  [模拟中线] 本次未买入（见 skipped 原因）", show_progress)
    return {
        "bought": bought,
        "skipped": skipped,
        "cash": mt["cash"],
        "position_count": len(positions),
    }


def check_midterm_exits(engine: SimReplayEngine, show_progress: bool = False) -> List[dict]:
    """检查模拟中线止盈止损 / 到期。"""
    mt = ensure_midterm_state(engine.state)
    positions = mt.get("positions", [])
    if not positions:
        return []

    cfg = MidtermSimConfig(**{**asdict(MidtermSimConfig()), **mt.get("config", {})})
    codes = [p["code"] for p in positions]
    quotes = get_realtime_quotes(codes)
    if quotes.empty:
        return []

    qmap = quotes.set_index("code")
    today = _today()
    closed: List[dict] = []
    remain = []

    for p in positions:
        code = p["code"]
        if code not in qmap.index:
            remain.append(p)
            continue
        q = qmap.loc[code]
        price = float(q["close"])
        low = float(q.get("low", price))
        high = float(q.get("high", price))
        buy_date = _norm_date(p["buy_date"])
        hold = _hold_days(buy_date, today)

        if not _is_sellable(buy_date, cfg, today):
            remain.append(p)
            continue

        sell_price = None
        reason = ""
        if low <= p["stop_loss"]:
            sell_price = p["stop_loss"]
            reason = f"止损({cfg.stop_loss_pct}%)"
        elif high >= p["take_profit"]:
            sell_price = p["take_profit"]
            reason = f"止盈({cfg.take_profit_pct}%)"
        elif hold >= cfg.max_hold_days:
            sell_price = price
            reason = f"持仓{cfg.max_hold_days}交易日到期"

        if sell_price is None:
            remain.append(p)
            continue

        profit_pct = (sell_price - p["buy_price"]) / p["buy_price"] * 100 if p["buy_price"] else 0
        profit_amount = (sell_price - p["buy_price"]) * p["quantity"]
        trade = {
            "code": code,
            "name": p["name"],
            "buy_date": p["buy_date"],
            "buy_price": p["buy_price"],
            "sell_date": today,
            "sell_price": round(sell_price, 2),
            "quantity": p["quantity"],
            "profit_pct": round(profit_pct, 2),
            "profit_amount": round(profit_amount, 2),
            "hold_days": hold,
            "exit_reason": reason,
            "midterm_score": p.get("midterm_score", 0),
        }
        mt["cash"] = float(mt.get("cash", 0)) + sell_price * p["quantity"]
        closed.append(trade)
        if show_progress:
            _progress(
                f"  [模拟中线] 卖出 {p['name']}({code}) @{sell_price:.2f} {reason} {profit_pct:+.1f}%",
                True,
            )

    mt["positions"] = remain
    if closed:
        mt.setdefault("closed_trades", [])
        mt["closed_trades"].extend(closed)
        mt["updated_at"] = datetime.now().isoformat()
        engine._save_state()
    return closed


def run_midterm_sim_review(engine: SimReplayEngine, show_progress: bool = False) -> List[dict]:
    """模拟中线持仓技术面复盘。"""
    from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor

    mt = ensure_midterm_state(engine.state)
    positions = mt.get("positions", [])
    if not positions:
        _progress("  [模拟中线] 无持仓，跳过复盘", show_progress)
        return []

    cfg = MidtermSimConfig(**{**asdict(MidtermSimConfig()), **mt.get("config", {})})
    capital = float(mt.get("initial_capital", cfg.capital))

    quotes = get_realtime_quotes([p["code"] for p in positions])
    qmap = quotes.set_index("code") if not quotes.empty else None

    holdings = []
    for p in positions:
        code = p["code"]
        px = float(qmap.loc[code, "close"]) if qmap is not None and code in qmap.index else p["buy_price"]
        mv = px * p["quantity"]
        cost = p["buy_price"] * p["quantity"]
        weight = mv / capital * 100 if capital else 0
        holdings.append({
            "code": code,
            "name": p["name"],
            "cost_price": p["buy_price"],
            "weight_pct": round(weight, 1),
            "profit_pct": round((px - p["buy_price"]) / p["buy_price"] * 100, 2) if p["buy_price"] else 0,
        })

    advisor = MidtermPortfolioAdvisor()
    reviews = advisor.review_holdings(holdings, show_progress=show_progress)
    mt["last_reviews"] = reviews
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()
    _progress(f"  [模拟中线] 复盘完成 {len(reviews)} 只", show_progress)
    return reviews


def _sim_held_codes(engine: SimReplayEngine) -> set[str]:
    mt = ensure_midterm_state(engine.state)
    held = {str(p["code"]).zfill(6) for p in mt.get("positions", [])}
    held |= {str(p["code"]).zfill(6) for p in engine.state.get("positions", [])}
    return held


def run_sim_midterm_select(
    engine: SimReplayEngine,
    *,
    show_progress: bool = False,
    force: bool = False,
    industry: Optional[str] = None,
    performance: Optional[str] = None,
    use_cache: bool = False,
    prefilter: int = 800,
) -> dict:
    """
    模拟盘中线选股主路径：三倍量「突破日」跟进（一阳穿三线）。

    证据（midterm_pick_tracker 满期）：三倍量突破胜率约 63% / 均益约 +5.8%；
    观察池缩量再买结算胜率约 15%，仅作无突破日候选时的次选补仓。
    """
    from quantpy.midterm_triple_volume_selector import get_breakout_day_recommendations
    from quantpy.triple_volume_watchlist import (
        get_buy_signal_recommendations,
        sync_and_evaluate_watchlist,
    )

    del industry, performance, use_cache, prefilter
    try:
        _progress("=" * 50, show_progress)
        _progress("模拟中线选股（三倍量突破日 · 15万账户）", show_progress)
        ensure_midterm_state(engine.state)
        held = _sim_held_codes(engine)

        # 次选：仍同步观察池，供无突破日标的时补位
        watch_result = sync_and_evaluate_watchlist(show_progress=show_progress)
        summary = watch_result.get("summary") or {}
        watching_n = int(summary.get("watching_count") or 0)
        signal_n = int(summary.get("buy_signal_count") or 0)

        breakout_recs = get_breakout_day_recommendations(
            force_select=force, show_progress=show_progress,
        )
        breakout_recs, price_skipped = _apply_today_buy_prices(breakout_recs)
        breakout_recs = [
            r for r in breakout_recs if str(r.get("code", "")).zfill(6) not in held
        ]

        entry_source = "triple_volume_breakout_day"
        buy_recs = breakout_recs
        if not buy_recs:
            watch_recs = get_buy_signal_recommendations(today_only=True)
            watch_recs, watch_skip = _apply_today_buy_prices(watch_recs)
            price_skipped.extend(watch_skip)
            watch_recs = [
                r for r in watch_recs if str(r.get("code", "")).zfill(6) not in held
            ]
            if watch_recs:
                entry_source = "triple_volume_watchlist_secondary"
                buy_recs = watch_recs
                _progress(
                    "  今日无突破日标的，降级使用观察池缩量买点（历史胜率偏低）",
                    show_progress,
                )

        closed = check_midterm_exits(engine, show_progress=show_progress)
        logged: List[dict] = []
        buy_result: dict = {"bought": [], "skipped": [], "message": "暂无可用买点"}

        if buy_recs:
            _progress(
                f"  候选 {len(buy_recs)} 只 · 来源 {entry_source}"
                f"（观察中 {watching_n} · 池信号 {signal_n}）",
                show_progress,
            )
            logged = record_midterm_picks(
                engine,
                buy_recs,
                show_progress=show_progress,
                source=entry_source,
            )
            buy_result = run_midterm_sim_buy(
                engine, buy_recs, show_progress=show_progress, force=force,
            )
            if price_skipped:
                buy_result.setdefault("skipped", []).extend(price_skipped)
        else:
            skip_hint = f"（过滤 {len(price_skipped)} 只）" if price_skipped else ""
            _progress(
                f"  今日暂无买点{skip_hint}（突破日 0 · 池信号 {signal_n} · 观察中 {watching_n}）",
                show_progress,
            )
            buy_result = {
                "bought": [],
                "skipped": price_skipped,
                "message": "今日无突破日/观察池买点",
            }

        reviews = run_midterm_sim_review(engine, show_progress=show_progress)

        buy_n = len(buy_result.get("bought", []))
        ok = bool(buy_recs or buy_n or watching_n or breakout_recs)

        if buy_n:
            message = f"{entry_source} 选取 {len(buy_recs)} 只，买入 {buy_n} 只"
        elif buy_recs:
            message = f"{entry_source} {len(buy_recs)} 只买点均未成交"
        else:
            message = buy_result.get("message") or "无买点"

        return {
            "ok": ok,
            "message": message,
            "closed": closed,
            "logged": logged,
            "buy_result": buy_result,
            "reviews": reviews,
            "strategy": entry_source,
            "buy_recommendations": buy_recs,
            "recommendations": buy_recs,
            "breakout_count": len(breakout_recs),
            "watchlist_summary": summary,
            "select_stats": {
                "strategy": entry_source,
                "source": entry_source,
                "breakout_day": entry_source.endswith("breakout_day"),
            },
            "summary": enrich_midterm_sim(engine.state),
        }
    except Exception as exc:
        _progress(f"  [模拟中线] 选股失败: {exc}", show_progress)
        return {
            "ok": False,
            "message": str(exc),
            "bought": [],
            "skipped": [],
            "strategy": "triple_volume_breakout_day",
            "summary": enrich_midterm_sim(engine.state),
        }


def apply_midterm_recommendations_to_sim(
    engine: SimReplayEngine,
    recommendations: List[dict],
    *,
    show_progress: bool = False,
    force: bool = False,
    source: str = "triple_volume_buy",
) -> dict:
    """观察池买点或外部推荐：检查卖出 → 记录选股 → 模拟买入 → 复盘。"""
    _progress("[模拟中线] 15万账户处理推荐…", show_progress)
    ensure_midterm_state(engine.state)
    closed = check_midterm_exits(engine, show_progress=show_progress)
    logged = record_midterm_picks(
        engine, recommendations, show_progress=show_progress, source=source,
    )
    buy_result = run_midterm_sim_buy(
        engine, recommendations, show_progress=show_progress, force=force,
    )
    reviews = run_midterm_sim_review(engine, show_progress=show_progress)
    return {
        "closed_today": closed,
        "pick_logged": len(logged),
        "bought": buy_result.get("bought", []),
        "skipped": buy_result.get("skipped", []),
        "reviews": reviews,
        "summary": enrich_midterm_sim(engine.state),
    }


def enrich_midterm_sim(state: dict, quotes_df: Optional[pd.DataFrame] = None) -> dict:
    """构造 API 用的模拟中线账户摘要。"""
    from quantpy.trade_math import mark_position

    mt = ensure_midterm_state(state)
    cfg = MidtermSimConfig(**{**asdict(MidtermSimConfig()), **mt.get("config", {})})
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
        sellable = _is_sellable(p["buy_date"], cfg, today)
        enriched.append({
            **p,
            "current_price": marked["current_price"],
            "market_value": marked["market_value"],
            "profit_amount": marked["profit_amount"],
            "profit_pct": marked["profit_pct"],
            "quote_ok": marked["quote_ok"],
            "weight_pct": round(marked["market_value"] / capital * 100, 2) if capital else 0,
            "sellable_today": sellable,
            "t_plus_one_locked": cfg.t_plus_one and not sellable,
        })

    cash = float(mt.get("cash", cfg.capital))
    equity = cash + total_mv
    initial = float(mt.get("initial_capital", cfg.capital))
    closed = list(mt.get("closed_trades", []))
    closed.sort(key=lambda x: x.get("sell_date", ""), reverse=True)
    pick_log = list(mt.get("pick_log", []))
    pick_log.sort(key=lambda x: (x.get("date", ""), x.get("midterm_score", 0)), reverse=True)

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
        "pick_log": pick_log[:20],
        "last_reviews": mt.get("last_reviews", []),
        "last_record_date": mt.get("last_record_date", ""),
        "last_buy_date": mt.get("last_buy_date", ""),
        "config": asdict(cfg),
        "strategy": "triple_volume",
        "updated_at": mt.get("updated_at", ""),
    }


# ---------------------------------------------------------------------------
# MA20 突破 + MA5 回踩 · 模拟账户（20 万）
# ---------------------------------------------------------------------------

DEFAULT_MA20_SIM_CAPITAL = 200_000.0


@dataclass
class MidtermMa20SimConfig:
    capital: float = DEFAULT_MA20_SIM_CAPITAL
    max_positions: int = 5
    max_single_weight_pct: float = 25.0
    stop_loss_pct: float = -8.0
    principal_withdraw_pct: float = 25.0  # 获利达此比例撤出本金
    principal_withdraw_min: float = 20.0
    max_hold_days: int = 45
    min_score: int = 62
    max_new_per_run: int = 2
    t_plus_one: bool = True


def default_midterm_ma20_state() -> dict:
    cfg = MidtermMa20SimConfig()
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
        "last_reviews": [],
        "updated_at": "",
        "strategy": "ma20_pullback",
    }


def ensure_midterm_ma20_state(state: dict) -> dict:
    if "midterm_ma20" not in state or not isinstance(state.get("midterm_ma20"), dict):
        state["midterm_ma20"] = default_midterm_ma20_state()
        return state["midterm_ma20"]
    mt = state["midterm_ma20"]
    defaults = default_midterm_ma20_state()
    for key, val in defaults.items():
        if key not in mt:
            mt[key] = val
    if "config" not in mt:
        mt["config"] = defaults["config"]
    return mt


def _ma20_sim_held_codes(engine: SimReplayEngine) -> set[str]:
    mt = ensure_midterm_ma20_state(engine.state)
    held = {str(p["code"]).zfill(6) for p in mt.get("positions", [])}
    held |= _sim_held_codes(engine)
    return held


def check_ma20_sim_exits(engine: SimReplayEngine, show_progress: bool = False) -> List[dict]:
    """MA20 策略出场：撤本金 / 5日下穿10日线 / 止损 / 到期。"""
    from quantpy.midterm_ma20_pullback_selector import check_ma20_trend_exit
    from quantpy.stock_data import get_stock_hist

    mt = ensure_midterm_ma20_state(engine.state)
    positions = mt.get("positions", [])
    if not positions:
        return []

    cfg = MidtermMa20SimConfig(**{**asdict(MidtermMa20SimConfig()), **mt.get("config", {})})
    codes = [p["code"] for p in positions]
    quotes = get_realtime_quotes(codes)
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
        profit_pct = (price - buy_price) / buy_price * 100 if buy_price else 0

        if not _is_sellable(buy_date, cfg, today):
            remain.append(p)
            continue

        sell_price = None
        reason = ""
        partial_qty = 0

        if low <= p.get("stop_loss", buy_price * 0.92):
            sell_price = p.get("stop_loss", buy_price * 0.92)
            reason = f"止损({cfg.stop_loss_pct}%)"
        elif hold >= cfg.max_hold_days:
            sell_price = price
            reason = f"持仓{cfg.max_hold_days}交易日到期"
        else:
            hist = get_stock_hist(code, days=30, patch_live=True)
            trend_exit, trend_reason = check_ma20_trend_exit(hist)
            if trend_exit:
                sell_price = price
                reason = trend_reason or "5日下穿10日线"
            elif (
                profit_pct >= cfg.principal_withdraw_min
                and not p.get("principal_withdrawn")
            ):
                cost = buy_price * qty
                sell_qty = int(cost / price / 100) * 100
                if sell_qty <= 0:
                    sell_qty = qty
                if sell_qty >= qty:
                    sell_price = price
                    reason = f"获利{profit_pct:.1f}%撤出本金(清仓)"
                else:
                    partial_qty = sell_qty
                    sell_price = price
                    reason = f"获利{profit_pct:.1f}%撤出本金(留{qty - sell_qty}股)"

        if sell_price is None:
            remain.append(p)
            continue

        if partial_qty > 0 and partial_qty < qty:
            proceeds = sell_price * partial_qty
            profit_amount = (sell_price - buy_price) * partial_qty
            trade = {
                "code": code,
                "name": p["name"],
                "buy_date": p["buy_date"],
                "buy_price": buy_price,
                "sell_date": today,
                "sell_price": round(float(sell_price), 2),
                "quantity": partial_qty,
                "profit_pct": round((sell_price - buy_price) / buy_price * 100, 2),
                "profit_amount": round(profit_amount, 2),
                "hold_days": hold,
                "exit_reason": reason,
                "midterm_score": p.get("midterm_score", 0),
                "strategy": "ma20_pullback",
            }
            closed.append(trade)
            mt["cash"] = round(float(mt.get("cash", 0)) + proceeds, 2)
            p = {**p, "quantity": qty - partial_qty, "principal_withdrawn": True}
            remain.append(p)
            _progress(
                f"  [MA20模拟] 部分卖出 {p['name']}({code}) {partial_qty}股 @{sell_price:.2f} {reason}",
                show_progress,
            )
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
            "midterm_score": p.get("midterm_score", 0),
            "strategy": "ma20_pullback",
        }
        closed.append(trade)
        mt["cash"] = round(float(mt.get("cash", 0)) + sell_price * qty, 2)
        _progress(
            f"  [MA20模拟] 卖出 {p['name']}({code}) @{sell_price:.2f} {reason}",
            show_progress,
        )

    mt["positions"] = remain
    if closed:
        mt.setdefault("closed_trades", []).extend(closed)
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()
    return closed


def run_ma20_sim_buy(
    engine: SimReplayEngine,
    recommendations: List[dict],
    *,
    show_progress: bool = False,
    force: bool = False,
) -> dict:
    """MA20 回踩模拟买入（上涨不加仓：已有仓则跳过）。"""
    mt = ensure_midterm_ma20_state(engine.state)
    cfg = MidtermMa20SimConfig(**{**asdict(MidtermMa20SimConfig()), **mt.get("config", {})})
    today = _today()
    positions = list(mt.get("positions", []))
    held = {str(p["code"]).zfill(6) for p in positions}
    cash = float(mt.get("cash", cfg.capital))
    bought: List[dict] = []
    skipped: List[dict] = []

    if not force and mt.get("last_buy_date") == today and len(positions) >= cfg.max_positions:
        return {"bought": [], "skipped": [{"reason": "今日已买满"}], "cash": cash}

    recs = sorted(recommendations, key=lambda x: x.get("midterm_score", 0), reverse=True)
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
        score = float(rec.get("midterm_score") or 0)
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
            "midterm_score": score,
            "reason": str(rec.get("reason") or "")[:120],
            "tags": str(rec.get("tags") or ""),
            "strategy": "ma20_pullback",
            "principal_withdrawn": False,
            "id": uuid.uuid4().hex[:10],
        }
        positions.append(pos)
        cash -= cost
        held.add(code)
        bought.append(pos)
        new_count += 1
        _progress(
            f"  [MA20模拟] 买入 {pos['name']}({code}) {qty}股 @{buy_price:.2f} 评分{score:.0f}",
            show_progress,
        )

    mt["positions"] = positions
    mt["cash"] = round(cash, 2)
    if bought:
        mt["last_buy_date"] = today
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()
    return {"bought": bought, "skipped": skipped, "cash": mt["cash"], "position_count": len(positions)}


def record_ma20_picks(
    engine: SimReplayEngine,
    recommendations: List[dict],
    *,
    show_progress: bool = False,
) -> List[dict]:
    mt = ensure_midterm_ma20_state(engine.state)
    today = _today()
    logged = []
    for rec in recommendations:
        entry = {
            "date": today,
            "code": str(rec.get("code", "")).zfill(6),
            "name": rec.get("name", ""),
            "midterm_score": rec.get("midterm_score"),
            "price": rec.get("price"),
            "reason": (rec.get("reason") or "")[:120],
            "action": "recorded",
            "source": "ma20_pullback",
        }
        mt.setdefault("pick_log", []).append(entry)
        logged.append(entry)
    mt["last_record_date"] = today
    mt["last_scan"] = recommendations
    mt["updated_at"] = datetime.now().isoformat()
    engine._save_state()
    _progress(f"  [MA20模拟] 记录选股 {len(logged)} 条", show_progress)
    return logged


def run_sim_ma20_pullback_select(
    engine: SimReplayEngine,
    *,
    show_progress: bool = False,
    force: bool = False,
    industry: Optional[str] = None,
    prefilter: int = 600,
) -> dict:
    """模拟 MA20 突破 + MA5 回踩选股（20 万账户）。"""
    from quantpy.midterm_ma20_pullback_selector import run_ma20_pullback_market_scan

    try:
        _progress("=" * 50, show_progress)
        _progress("模拟中线选股（MA20突破·MA5回踩 · 20万账户）", show_progress)
        ensure_midterm_ma20_state(engine.state)
        held = _ma20_sim_held_codes(engine)

        recs, select_stats = run_ma20_pullback_market_scan(
            exclude_codes=sorted(held),
            top_n=20,
            prefilter=prefilter,
            show_progress=show_progress,
            industry=industry,
        )

        closed = check_ma20_sim_exits(engine, show_progress=show_progress)
        logged = record_ma20_picks(engine, recs, show_progress=show_progress)
        buy_result = run_ma20_sim_buy(engine, recs, show_progress=show_progress, force=force)
        reviews = run_midterm_sim_review(engine, show_progress=show_progress)

        buy_n = len(buy_result.get("bought", []))
        message = (
            f"MA20回踩扫描命中 {len(recs)} 只，买入 {buy_n} 只"
            if recs
            else "今日暂无 MA20 突破回踩买点"
        )
        _progress(f"  完成：{message}", show_progress)
        return {
            # 扫描完成即成功；0 命中是买点稀疏的正常结果，勿当失败
            "ok": True,
            "message": message,
            "strategy": "ma20_pullback",
            "buy_recommendations": recs,
            "recommendations": recs,
            "select_stats": select_stats,
            "closed_today": closed,
            "pick_logged": len(logged),
            "bought": buy_result.get("bought", []),
            "skipped": buy_result.get("skipped", []),
            "reviews": reviews,
            "summary": enrich_midterm_ma20_sim(engine.state),
            "hit_count": len(recs),
        }
    except Exception as exc:
        import traceback

        err = traceback.format_exc()
        _progress(f"  [失败] {exc}", show_progress)
        return {
            "ok": False,
            "message": f"MA20选股失败: {exc}",
            "error": err,
            "summary": enrich_midterm_ma20_sim(engine.state),
        }


def enrich_midterm_ma20_sim(state: dict, quotes_df: Optional[pd.DataFrame] = None) -> dict:
    from quantpy.trade_math import mark_position

    mt = ensure_midterm_ma20_state(state)
    cfg = MidtermMa20SimConfig(**{**asdict(MidtermMa20SimConfig()), **mt.get("config", {})})
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
            "principal_withdrawn": bool(p.get("principal_withdrawn")),
        })

    cash = float(mt.get("cash", cfg.capital))
    equity = cash + total_mv
    initial = float(mt.get("initial_capital", cfg.capital))
    closed = list(mt.get("closed_trades", []))
    closed.sort(key=lambda x: x.get("sell_date", ""), reverse=True)
    pick_log = list(mt.get("pick_log", []))
    last_scan = list(mt.get("last_scan", []))

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
        "pick_log": pick_log[:20],
        "last_scan": last_scan[:20],
        "last_reviews": mt.get("last_reviews", []),
        "config": asdict(cfg),
        "strategy": "ma20_pullback",
        "updated_at": mt.get("updated_at", ""),
    }
