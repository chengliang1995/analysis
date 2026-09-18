"""仪表盘数据组装（从 web_app 抽出，路由层保持薄）。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from quantpy.json_util import df_to_records_safe
from quantpy.paths import OUTPUT_DIR, PROJECT_ROOT, REPORT_DIR
from quantpy.portfolio import PortfolioManager
from quantpy.sim_replay import SimReplayEngine
from quantpy.sim_midterm import enrich_midterm_sim, enrich_midterm_ma20_sim
from quantpy.ai_learning_optimizer import load_latest_ai_learning
from quantpy.midterm_portfolio_advisor import (
    MidtermPortfolioAdvisor,
    ensure_daily_midterm_operations,
    load_latest_midterm_advice,
)
from quantpy.midterm_triple_volume_selector import load_latest_triple_volume_advice
from quantpy.triple_volume_watchlist import load_watchlist_summary
from quantpy.midterm_pick_tracker import load_tracker_summary
from quantpy.midterm_level_alerts import scan_midterm_level_alerts
from quantpy.stock_data import get_realtime_quotes
from quantpy.sector_recommender import load_latest_sector
from quantpy.real_portfolio_reviewer import load_latest_real_review
from quantpy.trade_journal import TradeJournal

BASE_DIR = PROJECT_ROOT


def _ultra_short_records(df: pd.DataFrame, top_n: int = 10) -> list[dict]:
    if df is None or df.empty:
        return []
    cols = [
        "code", "name", "ultra_short_score", "pct_chg", "turnover",
        "consecutive_boards", "strength_factor", "is_sealed_board",
        "is_strong_today", "hold_no_sell", "tags",
        "scan_price", "buy_price_ref", "sell_price_ref",
        "stop_loss_ref", "take_profit_ref", "buy_zone",
    ]
    available = [c for c in cols if c in df.columns]
    rows = df.head(top_n)[available].copy()
    if "code" in rows.columns:
        rows["code"] = rows["code"].astype(str).str.zfill(6)
    return df_to_records_safe(rows)


def load_cached_ultra_short(top_n: int = 10) -> list[dict]:
    from quantpy.daily_advisor import load_ultra_short_scan_cache

    df = load_ultra_short_scan_cache()
    if not df.empty:
        return _ultra_short_records(df, top_n=top_n)

    # 回退：日报 JSON 摘要（定时任务 report 阶段产出）
    summary_files = sorted(REPORT_DIR.glob("daily_summary_*.json"), reverse=True)
    if summary_files:
        try:
            summary = json.loads(summary_files[0].read_text(encoding="utf-8"))
            top = summary.get("ultra_short_top10") or []
            if top:
                return top[:top_n]
        except (OSError, json.JSONDecodeError):
            pass
    return []


def load_latest_report_meta() -> dict:
    files = sorted(REPORT_DIR.glob("daily_report_*.md"), reverse=True)
    if not files:
        return {"name": "", "path": "", "updated_at": ""}
    path = files[0]
    return {
        "name": path.name,
        "path": str(path.relative_to(BASE_DIR)).replace("\\", "/"),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def load_latest_report_content() -> dict:
    files = sorted(REPORT_DIR.glob("daily_report_*.md"), reverse=True)
    if not files:
        return {"name": "", "content": ""}
    path = files[0]
    return {"name": path.name, "content": path.read_text(encoding="utf-8")}


def _file_mtime_meta(path: Path) -> dict:
    if not path.exists():
        return {"path": "", "updated_at": ""}
    return {
        "path": str(path.relative_to(BASE_DIR)).replace("\\", "/"),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def load_scheduler_status() -> dict:
    """读取计划任务状态（委托 scheduler_status 模块）。"""
    from quantpy.scheduler_status import load_scheduler_status as _load

    return _load()


def load_data_freshness() -> dict:
    """各模块缓存文件的最近更新时间（与定时任务产出对齐）。"""
    today = datetime.now().strftime("%Y%m%d")
    ultra_path = OUTPUT_DIR / f"ultra_short_{today}.csv"
    if not ultra_path.exists():
        ultra_files = sorted(OUTPUT_DIR.glob("ultra_short_*.csv"), reverse=True)
        ultra_path = ultra_files[0] if ultra_files else ultra_path
    midterm_files = sorted((OUTPUT_DIR / "midterm").glob("midterm_*.json"), reverse=True)
    tv_files = sorted((OUTPUT_DIR / "midterm").glob("triple_volume_*.json"), reverse=True)
    return {
        "ultra_short": _file_mtime_meta(ultra_path),
        "midterm": _file_mtime_meta(midterm_files[0]) if midterm_files else {"path": "", "updated_at": ""},
        "triple_volume": _file_mtime_meta(tv_files[0]) if tv_files else {"path": "", "updated_at": ""},
        "report": load_latest_report_meta(),
    }


def _resolve_midterm(portfolio_stats: dict) -> dict:
    """仪表盘用：优先读定时任务/手动分析落盘的缓存。"""
    cached = load_latest_midterm_advice()
    if cached.get("reviews") or cached.get("recommendations"):
        return cached
    if not portfolio_stats.get("has_data"):
        return {}
    try:
        return MidtermPortfolioAdvisor().run_quick_advice(portfolio_stats)
    except Exception:
        return {}


def _enrich_portfolio_with_midterm(
    portfolio_stats: dict,
    midterm: dict,
    level_alerts: Optional[dict] = None,
) -> dict:
    stats = dict(portfolio_stats)
    positions = list(stats.get("positions", []))
    review_map = {r["code"]: r for r in midterm.get("reviews", []) if r.get("ok")}
    alert_map = {
        str(a["code"]).zfill(6): a
        for a in (level_alerts or {}).get("alerts", [])
    }
    for p in positions:
        code = str(p["code"]).zfill(6)
        r = review_map.get(code, {})
        p["midterm_trend"] = r.get("trend", "")
        p["midterm_score"] = r.get("midterm_score", "")
        p["midterm_action"] = r.get("action", "")
        p["midterm_rsi"] = r.get("rsi", "")
        p["midterm_support"] = r.get("support", "")
        p["midterm_resistance"] = r.get("resistance", "")
        alert = alert_map.get(code)
        if alert:
            p["level_alert"] = alert
            p["level_alert_label"] = alert.get("alert_label", "")
            p["level_alert_signal"] = alert.get("signal_label", "")
    stats["positions"] = positions
    daily_ops = ensure_daily_midterm_operations(midterm, stats, level_alerts)
    if daily_ops:
        midterm = dict(midterm)
        midterm["daily_operations"] = daily_ops
    stats["midterm"] = midterm
    stats["midterm_daily_operations"] = daily_ops
    return stats


def get_suggestions(
    portfolio_stats: Optional[dict] = None,
    midterm: Optional[dict] = None,
    level_alerts: Optional[dict] = None,
) -> dict:
    pm = PortfolioManager()
    ultra = load_cached_ultra_short(top_n=10)
    if portfolio_stats is None:
        portfolio_stats = pm.analyze() if pm.list_positions() else {}
    if midterm is None:
        midterm = _resolve_midterm(portfolio_stats)

    portfolio_actions = pm.generate_action_suggestions(
        ultra_short=ultra,
        midterm_advice=midterm if midterm else None,
    )
    portfolio_summary = pm.generate_suggestions() if pm.list_positions() else []

    journal = TradeJournal()
    learn = journal.generate_suggestions(days=30)

    ai_learn: list[str] = []
    ai_meta = load_latest_ai_learning()
    if ai_meta.get("suggestions"):
        ai_learn = list(ai_meta["suggestions"][:8])
        if ai_meta.get("param_changes"):
            ai_learn.append(
                "模拟参数: "
                + ", ".join(f"{k} {v}" for k, v in ai_meta["param_changes"].items())
            )
        if ai_meta.get("selection_changes"):
            sel = ai_meta["selection_changes"]
            parts = []
            if sel.get("ultra_min_score") is not None:
                parts.append(f"超短≥{sel['ultra_min_score']}")
            if sel.get("midterm_min_score") is not None:
                parts.append(f"中线≥{sel['midterm_min_score']}")
            if sel.get("ultra_preferred_tags"):
                parts.append("偏好" + "/".join(sel["ultra_preferred_tags"][:2]))
            if parts:
                ai_learn.append("选股参数: " + " · ".join(parts))

    tracker_payload = load_tracker_summary(evaluate=False)
    midterm_tracker_learn = list(tracker_payload.get("suggestions") or [])[:6]

    midterm_review = [r.get("summary", "") for r in midterm.get("reviews", []) if r.get("ok")][:6]
    midterm_optimize = list(midterm.get("optimize_suggestions", []))[:5]
    midterm_daily = list((midterm.get("daily_operations") or {}).get("lines", []))[:8]
    midterm_recommend = [
        f"【推荐】{r['name']}({r['code']}) 评分{r['midterm_score']} · {r.get('reason', '')}"
        for r in midterm.get("recommendations", [])[:5]
    ]
    ultra_recommend = []
    for u in ultra[:5]:
        buy_ref = u.get("buy_price_ref") or u.get("scan_price") or u.get("price")
        stop_ref = u.get("stop_loss_ref")
        take_ref = u.get("take_profit_ref")
        ref_parts = [f"买{buy_ref}"] if buy_ref else []
        if stop_ref:
            ref_parts.append(f"止损{stop_ref}")
        if take_ref:
            ref_parts.append(f"止盈{take_ref}")
        ref_txt = " · ".join(ref_parts)
        ultra_recommend.append(
            f"【超短】{u.get('name')}({u.get('code')}) 评分{u.get('ultra_short_score')} "
            f"{ref_txt} · {u.get('tags', '')[:40]}"
        )

    real_review = load_latest_real_review()
    real_review_suggestions = list(real_review.get("optimization_suggestions", []))[:8]

    if level_alerts is None:
        level_alerts = scan_midterm_level_alerts(
            portfolio_stats,
            midterm.get("reviews") if midterm else None,
        )
    level_alert_msgs = list(level_alerts.get("messages", []))[:10]

    all_suggestions: list[str] = []
    seen: set[str] = set()
    for s in (
        level_alert_msgs + portfolio_actions + portfolio_summary
        + midterm_daily + midterm_review
        + midterm_optimize + midterm_recommend + ultra_recommend
        + midterm_tracker_learn + real_review_suggestions + learn + ai_learn
    ):
        if s and s not in seen:
            seen.add(s)
            all_suggestions.append(s)

    return {
        "portfolio_actions": portfolio_actions,
        "portfolio_summary": portfolio_summary,
        "midterm_review": midterm_review,
        "midterm_daily": midterm_daily,
        "midterm_optimize": midterm_optimize,
        "midterm_recommend": midterm_recommend,
        "ultra_recommend": ultra_recommend,
        "midterm_tracker": midterm_tracker_learn,
        "real_review": real_review_suggestions,
        "level_alerts": level_alert_msgs,
        "learn": learn,
        "ai_learn": ai_learn,
        "all": all_suggestions,
    }


def get_trades_data(days: int = 30) -> dict:
    journal = TradeJournal()
    df = journal.list_trades(days=days)
    stats = journal.analyze(days=days)
    return {
        "trades": df_to_records_safe(df),
        "stats": stats,
    }


def _enrich_sim_portfolio(
    engine: SimReplayEngine,
    quotes_df: Optional[pd.DataFrame] = None,
) -> dict:
    positions = list(engine.state.get("positions", []))
    if quotes_df is not None and not quotes_df.empty:
        qmap = quotes_df.copy()
        qmap["code"] = qmap["code"].astype(str).str.zfill(6)
        qmap = qmap.set_index("code")
    elif positions:
        quotes = get_realtime_quotes([p["code"] for p in positions])
        qmap = quotes.set_index("code") if quotes is not None and not quotes.empty else None
    else:
        qmap = None
    today = datetime.now().strftime("%Y-%m-%d")

    enriched = []
    total_market_value = 0.0
    for p in positions:
        code = str(p["code"]).zfill(6)
        current = float(qmap.loc[code, "close"]) if qmap is not None and code in qmap.index else p["buy_price"]
        cost_amount = p["buy_price"] * p["quantity"]
        market_value = current * p["quantity"]
        total_market_value += market_value
        profit_pct = (current - p["buy_price"]) / p["buy_price"] * 100 if p["buy_price"] else 0.0
        sellable = engine._is_sellable(p["buy_date"], today)
        enriched.append({
            **p,
            "current_price": round(current, 2),
            "sell_price_ref": round(current, 2),
            "market_value": round(market_value, 2),
            "profit_amount": round(market_value - cost_amount, 2),
            "profit_pct": round(profit_pct, 2),
            "weight_pct": 0.0,
            "sellable_today": sellable,
            "t_plus_one_locked": engine.config.t_plus_one and not sellable,
        })

    cash = float(engine.state.get("cash", 0))
    equity = cash + total_market_value
    if enriched:
        for row in enriched:
            row["weight_pct"] = round(row["market_value"] / equity * 100, 2) if equity > 0 else 0.0

    initial = float(engine.state.get("initial_capital", engine.config.capital))
    closed = list(engine.state.get("closed_trades", []))
    closed.sort(key=lambda x: x.get("sell_date", ""), reverse=True)

    return {
        "has_data": True,
        "bucket": "ultra_short",
        "initial_capital": initial,
        "cash": round(cash, 2),
        "market_value": round(total_market_value, 2),
        "equity": round(equity, 2),
        "total_return_pct": round((equity - initial) / initial * 100, 2) if initial else 0.0,
        "closed_count": len(closed),
        "trading_day_count": engine.state.get("trading_day_count", 0),
        "position_count": len(enriched),
        "positions": enriched,
        "closed_trades": closed[:10],
        "config": {
            "t_plus_one": engine.config.t_plus_one,
            "stop_loss_pct": engine.config.stop_loss_pct,
            "take_profit_pct": engine.config.take_profit_pct,
            "max_hold_days": engine.config.max_hold_days,
            "min_score": engine.config.min_score,
            "max_positions": engine.config.max_positions,
            "buy_premium_pct": engine.config.buy_premium_pct,
            "price_mode": "扫描价",
        },
        "updated_at": engine.state.get("updated_at", ""),
        "ai_learning": load_latest_ai_learning(),
        "midterm": enrich_midterm_sim(engine.state, quotes_df=quotes_df),
        "midterm_ma20": enrich_midterm_ma20_sim(engine.state, quotes_df=quotes_df),
    }


def _collect_holding_codes() -> list[str]:
    pm = PortfolioManager()
    sim_engine = SimReplayEngine()
    sim_engine.reload_state()
    codes = {
        str(p.code).zfill(6) for p in pm.list_positions()
    } | {
        str(p["code"]).zfill(6) for p in sim_engine.state.get("positions", [])
    } | {
        str(p["code"]).zfill(6)
        for p in sim_engine.state.get("midterm", {}).get("positions", [])
    } | {
        str(p["code"]).zfill(6)
        for p in sim_engine.state.get("midterm_ma20", {}).get("positions", [])
    }
    return sorted(codes)


def refresh_holdings_quotes() -> tuple[dict, dict, str]:
    """仅刷新实盘 + 模拟盘持仓行情（不扫全市场、不重跑中线分析）。"""
    codes = _collect_holding_codes()
    quotes = get_realtime_quotes(codes, verbose=False) if codes else pd.DataFrame()

    pm = PortfolioManager()
    portfolio_stats = pm.analyze(spot_df=quotes if not quotes.empty else None)

    sim_engine = SimReplayEngine()
    sim_engine.reload_state()
    sim_data = _enrich_sim_portfolio(
        sim_engine,
        quotes_df=quotes if not quotes.empty else None,
    )

    n_real = len(portfolio_stats.get("positions", []))
    n_sim = sim_data.get("position_count", 0)
    if not codes:
        log = "暂无持仓，未请求行情"
    else:
        log = f"已刷新 {len(codes)} 只持仓行情（实盘 {n_real} · 模拟 {n_sim}）"
    return portfolio_stats, sim_data, log


def get_portfolio_data(
    portfolio_stats: Optional[dict] = None,
    midterm: Optional[dict] = None,
    level_alerts: Optional[dict] = None,
) -> dict:
    if portfolio_stats is None:
        portfolio_stats = PortfolioManager().analyze()
    if midterm is None:
        midterm = _resolve_midterm(portfolio_stats)
    if level_alerts is None:
        level_alerts = scan_midterm_level_alerts(
            portfolio_stats,
            midterm.get("reviews") if midterm else None,
        )
    return _enrich_portfolio_with_midterm(portfolio_stats, midterm, level_alerts)


def get_sim_data() -> dict:
    engine = SimReplayEngine()
    engine.reload_state()
    return _enrich_sim_portfolio(engine)


def get_dashboard_data(
    portfolio_stats: Optional[dict] = None,
    sim_data: Optional[dict] = None,
    midterm: Optional[dict] = None,
) -> dict:
    if portfolio_stats is None:
        portfolio_stats = PortfolioManager().analyze()
    if sim_data is None:
        sim_data = get_sim_data()
    if midterm is None:
        midterm = _resolve_midterm(portfolio_stats)
    level_alerts = scan_midterm_level_alerts(
        portfolio_stats,
        midterm.get("reviews") if midterm else None,
    )
    sug = get_suggestions(
        portfolio_stats=portfolio_stats,
        midterm=midterm,
        level_alerts=level_alerts,
    )
    review = load_latest_real_review()
    try:
        # 仪表盘只读缓存摘要，避免每次刷新都拉 K 线评估
        midterm_tracker = load_tracker_summary(evaluate=False)
    except Exception:
        midterm_tracker = {
            "summary": {},
            "tracking": [],
            "matured_recent": [],
            "suggestions": [],
        }
    return {
        "portfolio": get_portfolio_data(
            portfolio_stats=portfolio_stats,
            midterm=midterm,
            level_alerts=level_alerts,
        ),
        "sim": sim_data,
        "ultra_short": load_cached_ultra_short(),
        "suggestions": sug["all"],
        "suggestion_groups": sug,
        "trades": get_trades_data(),
        "portfolio_review": {
            "has_data": bool(review.get("has_data")),
            "summary": review.get("summary", {}),
            "trade_reviews": review.get("trade_reviews", [])[:15],
            "generated_at": review.get("generated_at", ""),
        },
        "level_alerts": level_alerts,
        "report": load_latest_report_meta(),
        "sector": load_latest_sector(),
        "midterm_tracker": midterm_tracker,
        "triple_volume": load_latest_triple_volume_advice(),
        "triple_volume_watchlist": load_watchlist_summary(evaluate=False),
        "scheduler": load_scheduler_status(),
        "data_freshness": load_data_freshness(),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


