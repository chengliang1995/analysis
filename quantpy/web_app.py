#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地 Web 仪表盘：个人持仓 + 模拟持仓 + 一键操作

用法:
  python web_app.py
  python web_app.py --port 5050
"""

from __future__ import annotations

import argparse
import io
import traceback
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from flask import Flask, jsonify, render_template, request

from quantpy import __version__ as APP_VERSION
from quantpy.paths import OUTPUT_DIR, LOG_DIR, PROJECT_ROOT, REPORT_DIR, RETENTION_DAYS, TEMPLATES_DIR
from quantpy.portfolio import PortfolioManager
from quantpy.retention import prune_retention_files
from quantpy.sim_replay import SimReplayEngine
from quantpy.ai_learning_optimizer import load_latest_ai_learning, run_ai_learning
from quantpy.midterm_portfolio_advisor import (
    MidtermPortfolioAdvisor,
    format_midterm_report_markdown,
    load_latest_midterm_advice,
    run_midterm_advice,
)
from quantpy.midterm_level_alerts import scan_midterm_level_alerts
from quantpy.stock_data import (
    get_instrument_index,
    get_realtime_quotes,
    get_stock_recent_bars,
    is_etf_code,
    lookup_instrument_by_code,
    lookup_instrument_by_name,
    price_step_for_code,
)
from quantpy.real_portfolio_reviewer import (
    load_latest_real_review,
    run_real_portfolio_review,
)
from quantpy.trade_journal import TradeJournal

BASE_DIR = PROJECT_ROOT
app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.after_request
def _no_cache(response):
    if request.path == "/" or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


def _ultra_short_records(df: pd.DataFrame, top_n: int = 10) -> list[dict]:
    if df is None or df.empty:
        return []
    cols = [
        "code", "name", "ultra_short_score", "pct_chg", "turnover",
        "consecutive_boards", "strength_factor", "is_sealed_board",
        "is_strong_today", "hold_no_sell", "tags",
    ]
    available = [c for c in cols if c in df.columns]
    rows = df.head(top_n)[available].copy()
    if "code" in rows.columns:
        rows["code"] = rows["code"].astype(str).str.zfill(6)
    return rows.to_dict("records")


def load_cached_ultra_short(top_n: int = 10) -> list[dict]:
    files = sorted(OUTPUT_DIR.glob("ultra_short_*.csv"), reverse=True)
    if not files:
        return []
    try:
        df = pd.read_csv(files[0], dtype={"code": str})
        return _ultra_short_records(df, top_n=top_n)
    except (OSError, pd.errors.ParserError, ValueError):
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


def _resolve_midterm(portfolio_stats: dict) -> dict:
    """仪表盘用：优先读缓存；无缓存时仅对持仓做轻量复盘（不扫全市场）。"""
    cached = load_latest_midterm_advice()
    if cached.get("reviews"):
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
    stats["midterm"] = midterm
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
                "最近 AI 参数调整: "
                + ", ".join(f"{k} {v}" for k, v in ai_meta["param_changes"].items())
            )

    midterm_review = [r.get("summary", "") for r in midterm.get("reviews", []) if r.get("ok")][:6]
    midterm_optimize = list(midterm.get("optimize_suggestions", []))[:5]
    midterm_recommend = [
        f"【推荐】{r['name']}({r['code']}) 评分{r['midterm_score']} · {r.get('reason', '')}"
        for r in midterm.get("recommendations", [])[:5]
    ]

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
        level_alert_msgs + portfolio_actions + portfolio_summary + midterm_review
        + midterm_optimize + midterm_recommend + real_review_suggestions + learn + ai_learn
    ):
        if s and s not in seen:
            seen.add(s)
            all_suggestions.append(s)

    return {
        "portfolio_actions": portfolio_actions,
        "portfolio_summary": portfolio_summary,
        "midterm_review": midterm_review,
        "midterm_optimize": midterm_optimize,
        "midterm_recommend": midterm_recommend,
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
        "trades": df.to_dict("records") if not df.empty else [],
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
        },
        "updated_at": engine.state.get("updated_at", ""),
        "ai_learning": load_latest_ai_learning(),
    }


def _collect_holding_codes() -> list[str]:
    pm = PortfolioManager()
    sim_engine = SimReplayEngine()
    sim_engine.reload_state()
    codes = {
        str(p.code).zfill(6) for p in pm.list_positions()
    } | {
        str(p["code"]).zfill(6) for p in sim_engine.state.get("positions", [])
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
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _today_log_path() -> Path:
    return LOG_DIR / f"web_{datetime.now().strftime('%Y%m%d')}.log"


def _append_action_log(action: str, text: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _today_log_path().open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp}] [{action}] {text.rstrip()}\n")


class _ActionLogWriter(io.TextIOBase):
    """Capture stdout and stream each line into the daily action log."""

    def __init__(self, buf: io.StringIO, action: str):
        self._buf = buf
        self._action = action
        self._pending = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buf.write(s)
        self._pending += s
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                _append_action_log(self._action, line)
        return len(s)

    def flush(self) -> None:
        if self._pending.strip():
            _append_action_log(self._action, self._pending.rstrip())
            self._pending = ""


def _run_quiet(func, *args, action: str = "", **kwargs) -> tuple[Any, str]:
    buf = io.StringIO()
    if action:
        _append_action_log(action, "开始")
    try:
        out = _ActionLogWriter(buf, action) if action else buf
        with redirect_stdout(out):
            result = func(*args, **kwargs)
        if action:
            out.flush()
        log = buf.getvalue()
        if action:
            _append_action_log(action, "完成")
        return result, log
    except Exception:
        if action:
            try:
                out.flush()
            except Exception:
                pass
        log = buf.getvalue() + "\n" + traceback.format_exc()
        if action:
            _append_action_log(action, f"失败\n{log}")
        return None, log


@app.route("/")
def index():
    return render_template("dashboard.html", app_version=APP_VERSION)


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(get_dashboard_data())


@app.route("/api/logs/today")
def api_logs_today():
    """返回当日 Web 操作日志（供前端轮询显示长任务进度）。"""
    path = _today_log_path()
    if not path.exists():
        return jsonify({"ok": True, "content": ""})
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500
    return jsonify({"ok": True, "content": content})


@app.route("/api/sim")
def api_sim():
    return jsonify(get_sim_data())


@app.route("/api/instruments/index")
def api_instruments_index():
    """A 股代码/名称索引（本地 cache/stock_list.csv）。"""
    items, updated_at = get_instrument_index()
    return jsonify({
        "ok": True,
        "count": len(items),
        "updated_at": updated_at,
        "items": items,
    })


@app.route("/api/instrument/lookup")
def api_instrument_lookup():
    """按代码或名称互查（名称不唯一时返回候选列表）。"""
    code = str(request.args.get("code") or "").strip()
    name = str(request.args.get("name") or "").strip()
    if code:
        hit = lookup_instrument_by_code(code)
        if not hit:
            return jsonify({"ok": False, "message": "未找到该代码"}), 404
        return jsonify({"ok": True, "match": hit, "matches": [hit]})
    if name:
        matches = lookup_instrument_by_name(name)
        if not matches:
            return jsonify({"ok": False, "message": "未找到该名称"}), 404
        match = matches[0] if len(matches) == 1 else None
        return jsonify({
            "ok": True,
            "match": match,
            "matches": matches,
            "message": None if match else f"找到 {len(matches)} 个匹配，请输入更完整名称",
        })
    return jsonify({"ok": False, "message": "请提供 code 或 name 参数"}), 400


@app.route("/api/instrument/<code>")
def api_instrument(code: str):
    """查询证券类型与实时简称（股票/ETF 录入辅助）。"""
    code = str(code).zfill(6)
    if not code.isdigit() or len(code) != 6:
        return jsonify({"ok": False, "message": "代码须为 6 位数字"}), 400

    etf = is_etf_code(code)
    cached = lookup_instrument_by_code(code)
    quotes = get_realtime_quotes([code])
    name = (cached or {}).get("name", "")
    price = None
    if not quotes.empty:
        row = quotes.iloc[0]
        quote_name = str(row.get("name") or "").strip()
        if quote_name:
            import re
            name = re.sub(r"^\d+[~～]?", "", quote_name).strip() or quote_name
        for col in ("close", "price"):
            if col in row and pd.notna(row[col]) and float(row[col]) > 0:
                price = round(float(row[col]), 3 if etf else 2)
                break

    return jsonify({
        "ok": True,
        "code": code,
        "name": name,
        "is_etf": etf,
        "asset_type": "etf" if etf else "stock",
        "price_step": price_step_for_code(code),
        "price": price,
    })


@app.route("/api/stock/<code>/history")
def api_stock_history(code: str):
    """个股下钻：近 N 日行情（默认 10 个交易日）。"""
    days = request.args.get("days", 10, type=int)
    name = str(request.args.get("name") or "").strip()
    code = str(code).zfill(6)
    bars = get_stock_recent_bars(code, days=days)
    if not bars:
        return jsonify({"ok": False, "message": f"无法获取 {code} 近期行情"}), 404

    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    first_close = closes[0] if closes[0] else 1.0
    return jsonify(
        {
            "ok": True,
            "code": code,
            "name": name,
            "days": len(bars),
            "bars": bars,
            "summary": {
                "latest_close": closes[-1],
                "period_high": max(highs),
                "period_low": min(lows),
                "period_change_pct": round((closes[-1] - first_close) / first_close * 100, 2),
            },
        }
    )


@app.route("/api/portfolio/level-alerts")
def api_portfolio_level_alerts():
    refresh = request.args.get("refresh", "false").lower() == "true"
    portfolio_stats = PortfolioManager().analyze()
    if refresh:
        midterm = MidtermPortfolioAdvisor().run_quick_advice(portfolio_stats)
    else:
        midterm = _resolve_midterm(portfolio_stats)
    result = scan_midterm_level_alerts(
        portfolio_stats,
        midterm.get("reviews") if midterm else None,
        save=refresh,
    )
    return jsonify(result)


@app.route("/api/portfolio/review")
def api_portfolio_review():
    days = request.args.get("days", 90, type=int)
    refresh = request.args.get("refresh", "false").lower() == "true"
    if refresh:
        result = run_real_portfolio_review(days=days, show_progress=False)
    else:
        result = load_latest_real_review()
        if not result:
            result = run_real_portfolio_review(days=days, show_progress=False)
    return jsonify(result)


@app.route("/api/report/latest")
def api_report_latest():
    return jsonify(load_latest_report_content())


@app.route("/api/trades", methods=["GET"])
def api_trades_list():
    days = request.args.get("days", 30, type=int)
    return jsonify(get_trades_data(days=days))


@app.route("/api/trades", methods=["POST"])
def api_trades_add():
    data = request.get_json(silent=True) or {}
    required = ("code", "name", "buy_date", "buy_price")
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"ok": False, "message": f"缺少字段: {', '.join(missing)}"}), 400

    try:
        code = str(data["code"]).zfill(6)
        name = str(data["name"]).strip()
        buy_date = str(data["buy_date"])[:10]
        buy_price = float(data["buy_price"])
        quantity = int(data.get("quantity") or 100)
        strategy = str(data.get("strategy") or "手动")
        note = str(data.get("note") or "")
        sync_portfolio = data.get("sync_portfolio", True) not in (False, "false", 0, "0")
        trade_action = str(data.get("trade_action") or "sell").lower()

        sell_price_raw = str(data.get("sell_price", "")).strip()
        sell_date_raw = str(data.get("sell_date", "")).strip()
        has_sell = bool(sell_price_raw)

        if trade_action == "sell" and not has_sell:
            return jsonify({"ok": False, "message": "卖出扣减持仓时需填写卖出价"}), 400
        if has_sell and float(sell_price_raw) <= 0:
            return jsonify({"ok": False, "message": "卖出价须大于 0"}), 400
        if has_sell and not sell_date_raw:
            return jsonify({"ok": False, "message": "填写卖出价时需同时填写卖出日期"}), 400

        record = None
        message = ""

        if has_sell:
            journal = TradeJournal()
            record = journal.add_trade(
                code=code,
                name=name,
                buy_date=buy_date,
                buy_price=buy_price,
                sell_date=sell_date_raw[:10],
                sell_price=float(sell_price_raw),
                quantity=quantity,
                strategy=strategy,
                note=note,
            )
            message = f"已录入 {record.name} 收益 {record.profit_pct:+.2f}%"
        elif trade_action == "buy":
            message = f"已记录买入 {name}({code})，未写入交易日记（未平仓）"
        else:
            return jsonify({
                "ok": False,
                "message": "请填写卖出价，或选择「买入增加持仓」仅同步实盘",
            }), 400

        if sync_portfolio:
            pm = PortfolioManager()
            if trade_action == "buy":
                ok, sync_msg = pm.apply_buy(
                    code, name, quantity, buy_price,
                    buy_date=buy_date, strategy=strategy, note=note,
                )
            else:
                ok, sync_msg = pm.apply_sell(
                    code,
                    quantity,
                    sell_price=float(sell_price_raw) if has_sell else None,
                    sell_date=sell_date_raw[:10] if sell_date_raw else "",
                    strategy=strategy,
                    note=note,
                )
            message += f"；{sync_msg}"

        payload = {
            "ok": True,
            "message": message,
            "data": get_dashboard_data(),
        }
        if record is not None:
            payload["trade"] = record.to_summary()
        return jsonify(payload)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "message": f"数据格式错误: {exc}"}), 400


@app.route("/api/portfolio/position", methods=["POST"])
def api_portfolio_upsert():
    data = request.get_json(silent=True) or {}
    required = ("code", "name", "quantity", "cost_price")
    missing = [k for k in required if str(data.get(k, "")).strip() == ""]
    if missing:
        return jsonify({"ok": False, "message": f"缺少字段: {', '.join(missing)}"}), 400

    try:
        pm = PortfolioManager()
        if data.get("ultra_short_capital") not in (None, "") or data.get("midterm_capital") not in (None, ""):
            pm.set_capital_buckets(
                ultra_short_capital=float(data["ultra_short_capital"])
                if data.get("ultra_short_capital") not in (None, "") else None,
                midterm_capital=float(data["midterm_capital"])
                if data.get("midterm_capital") not in (None, "") else None,
            )
        elif data.get("total_capital") not in (None, ""):
            pm.set_total_capital(float(data["total_capital"]))
        pm.upsert_position(
            code=str(data["code"]).zfill(6),
            name=str(data["name"]).strip(),
            quantity=int(data["quantity"]),
            cost_price=float(data["cost_price"]),
            buy_date=str(data.get("buy_date") or "")[:10],
            strategy=str(data.get("strategy") or "手动"),
            note=str(data.get("note") or ""),
        )
        stats = pm.analyze()
        return jsonify({
            "ok": True,
            "message": f"已更新持仓 {data['name']}({str(data['code']).zfill(6)})，已同步配置",
            "portfolio": stats,
            "data": get_dashboard_data(),
        })
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "message": f"数据格式错误: {exc}"}), 400


@app.route("/api/portfolio/position/<code>/cost", methods=["PATCH"])
def api_portfolio_update_cost(code: str):
    data = request.get_json(silent=True) or {}
    cost_raw = str(data.get("cost_price", "")).strip()
    if not cost_raw:
        return jsonify({"ok": False, "message": "缺少 cost_price"}), 400
    try:
        cost_price = float(cost_raw)
        if cost_price <= 0:
            raise ValueError("成本价须大于 0")
        pm = PortfolioManager()
        ok, msg = pm.update_position_cost(code, cost_price)
        if not ok:
            return jsonify({"ok": False, "message": msg}), 404
        return jsonify({
            "ok": True,
            "message": f"已更新成本：{msg}，已同步配置",
            "data": get_dashboard_data(),
        })
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "message": f"数据格式错误: {exc}"}), 400


@app.route("/api/portfolio/position/<code>", methods=["DELETE"])
def api_portfolio_remove(code: str):
    pm = PortfolioManager()
    code = str(code).zfill(6)
    if not pm.remove_position(code):
        return jsonify({"ok": False, "message": f"未找到持仓 {code}"}), 404
    return jsonify({
        "ok": True,
        "message": f"已删除持仓 {code}，已同步配置",
        "data": get_dashboard_data(),
    })


@app.route("/api/actions/<action>", methods=["POST"])
def api_action(action: str):
    force = request.args.get("force", "false").lower() == "true"
    days = request.args.get("days", 20, type=int)
    log = ""
    message = ""
    extra: dict = {}
    prefetched_dashboard: Optional[dict] = None

    try:
        if action == "refresh":
            portfolio_stats, sim_data, log = refresh_holdings_quotes()
            n_real = len(portfolio_stats.get("positions", []))
            n_sim = sim_data.get("position_count", 0)
            message = f"持仓行情已刷新（实盘 {n_real} 只 · 模拟 {n_sim} 只）"
            prefetched_dashboard = get_dashboard_data(
                portfolio_stats=portfolio_stats,
                sim_data=sim_data,
            )
        elif action == "report":
            from quantpy.daily_advisor import generate_daily_report

            path, log = _run_quiet(
                generate_daily_report,
                top_prefilter=200,
                min_score=35,
                days=30,
            )
            if path is None:
                return jsonify({
                    "ok": False,
                    "message": "日报生成失败，请查看日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            message = "日报已生成"
            extra["report"] = load_latest_report_meta()
            extra["report_content"] = load_latest_report_content()
        elif action == "sim":
            engine = SimReplayEngine()
            result, log = _run_quiet(engine.run_daily, force_select=force, show_progress=False)
            closed = result.get("closed_today", 0) if isinstance(result, dict) else 0
            picks = result.get("picks_today", 0) if isinstance(result, dict) else 0
            message = f"模拟运行完成：平仓 {closed} 笔，新选 {picks} 只"
        elif action == "sim-review":
            engine = SimReplayEngine()
            review, log = _run_quiet(engine.run_review, show_progress=False)
            round_no = review.get("round", 0) if isinstance(review, dict) else 0
            message = f"第 {round_no} 轮复盘完成"
            if isinstance(review, dict):
                extra["review"] = {
                    "round": review.get("round"),
                    "suggestions": review.get("suggestions", []),
                    "ai_learning": review.get("ai_learning"),
                }
        elif action == "ai-learn":
            result, log = _run_quiet(run_ai_learning, show_progress=False, auto_apply=True)
            round_no = result.get("round", 0) if isinstance(result, dict) else 0
            message = f"AI 策略学习完成（第 {round_no} 轮）"
            extra["ai_learning"] = result
        elif action == "midterm":
            pm_stats = PortfolioManager().analyze()
            if not pm_stats.get("has_data"):
                return jsonify({"ok": False, "message": "暂无实盘持仓"}), 400
            result, log = _run_quiet(
                run_midterm_advice,
                pm_stats,
                show_progress=True,
                full=True,
                action="midterm",
            )
            if not isinstance(result, dict):
                return jsonify({
                    "ok": False,
                    "message": "中线分析失败，请查看运行日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            alerts = scan_midterm_level_alerts(
                pm_stats, result.get("reviews"), save=True,
            )
            alert_n = alerts.get("alert_count", 0)
            rec_n = len(result.get("recommendations", []))
            message = (
                f"中线分析完成：复盘 {len(result.get('reviews', []))} 只，推荐 {rec_n} 只"
                + (f"，{alert_n} 条价位提醒" if alert_n else "")
            )
            extra["midterm"] = result
            extra["level_alerts"] = alerts
            extra["midterm_content"] = {
                "name": "实盘中线分析报告",
                "content": result.get("markdown") or format_midterm_report_markdown(result),
            }
            prefetched_dashboard = get_dashboard_data(
                portfolio_stats=pm_stats,
                midterm=result,
            )
            prefetched_dashboard["level_alerts"] = alerts
        elif action == "alerts":
            pm_stats = PortfolioManager().analyze()
            if not pm_stats.get("has_data"):
                return jsonify({"ok": False, "message": "暂无实盘持仓"}), 400
            midterm, log = _run_quiet(
                MidtermPortfolioAdvisor().run_quick_advice, pm_stats,
            )
            result = scan_midterm_level_alerts(
                pm_stats, midterm.get("reviews") if isinstance(midterm, dict) else None, save=True,
            )
            n = result.get("alert_count", 0)
            message = f"价位提醒检查完成：{n} 条" if n else "价位提醒检查完成：暂无触发"
            extra["level_alerts"] = result
        elif action == "sim-backtest":
            engine = SimReplayEngine()
            result, log = _run_quiet(engine.replay_backtest, days=days, show_progress=False)
            if isinstance(result, dict) and result:
                message = (
                    f"回测完成：权益 {result.get('equity', 0):,.0f} 元 "
                    f"({result.get('total_return_pct', 0):+.2f}%)，"
                    f"平仓 {result.get('closed_count', 0)} 笔"
                )
                extra["backtest"] = result
            else:
                message = "回测完成"
        elif action == "review":
            result, log = _run_quiet(run_real_portfolio_review, days=90, show_progress=False)
            count = result.get("summary", {}).get("trade_count", 0) if isinstance(result, dict) else 0
            message = f"实盘复盘完成：分析 {count} 笔平仓"
            extra["portfolio_review"] = result
            if isinstance(result, dict) and result.get("markdown"):
                extra["review_content"] = {
                    "name": "实盘操作复盘",
                    "content": result["markdown"],
                }
        elif action == "scan":
            from quantpy.daily_advisor import run_ultra_short_scan

            df, log = _run_quiet(run_ultra_short_scan, top_prefilter=200, min_score=35)
            count = len(df) if df is not None and not df.empty else 0
            message = f"超短扫描完成，命中 {count} 只"
            extra["ultra_short"] = _ultra_short_records(df) if count else []
        else:
            return jsonify({"ok": False, "message": f"未知操作: {action}"}), 400

        payload = {
            "ok": True,
            "message": message,
            "log": log.strip(),
            "data": prefetched_dashboard or get_dashboard_data(),
            **extra,
        }
        if extra.get("ultra_short") is not None:
            payload["data"]["ultra_short"] = extra["ultra_short"]
        return jsonify(payload)
    except Exception as exc:
        return jsonify({
            "ok": False,
            "message": str(exc),
            "log": log.strip() + "\n" + traceback.format_exc(),
            "data": get_dashboard_data(),
        }), 500


def main(host: str = "127.0.0.1", port: int = 5050, debug: bool = False) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    pruned = prune_retention_files()
    if pruned:
        n = sum(len(v) for v in pruned.values())
        print(f"已清理 {n} 个超过 {RETENTION_DAYS} 天的历史文件")
    print(f"仪表盘: http://{host}:{port}")
    print("按 Ctrl+C 停止")
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QuantPy 本地 Web 仪表盘")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    main(host=args.host, port=args.port, debug=args.debug)
