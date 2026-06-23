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

from portfolio import PortfolioManager
from sim_replay import SimReplayEngine
from ai_learning_optimizer import load_latest_ai_learning, run_ai_learning
from midterm_portfolio_advisor import (
    MidtermPortfolioAdvisor,
    load_latest_midterm_advice,
    run_midterm_advice,
)
from stock_data import collect_daily_market_close, get_realtime_quotes
from trade_journal import TradeJournal

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
REPORT_DIR = OUTPUT_DIR / "daily_reports"
APP_VERSION = "2.5.1"
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
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


def _enrich_portfolio_with_midterm(portfolio_stats: dict, midterm: dict) -> dict:
    stats = dict(portfolio_stats)
    positions = list(stats.get("positions", []))
    review_map = {r["code"]: r for r in midterm.get("reviews", []) if r.get("ok")}
    for p in positions:
        r = review_map.get(str(p["code"]).zfill(6), {})
        p["midterm_trend"] = r.get("trend", "")
        p["midterm_score"] = r.get("midterm_score", "")
        p["midterm_action"] = r.get("action", "")
        p["midterm_rsi"] = r.get("rsi", "")
    stats["positions"] = positions
    stats["midterm"] = midterm
    return stats


def get_suggestions(
    portfolio_stats: Optional[dict] = None,
    midterm: Optional[dict] = None,
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

    all_suggestions: list[str] = []
    seen: set[str] = set()
    for s in (
        portfolio_actions + portfolio_summary + midterm_review
        + midterm_optimize + midterm_recommend + learn + ai_learn
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


def _enrich_sim_portfolio(engine: SimReplayEngine) -> dict:
    summary = engine.get_summary()
    equity = summary["equity"] or 1.0
    positions = summary.get("positions", [])
    quotes = get_realtime_quotes([p["code"] for p in positions]) if positions else None
    qmap = quotes.set_index("code") if quotes is not None and not quotes.empty else None
    today = datetime.now().strftime("%Y-%m-%d")

    enriched = []
    for p in positions:
        code = p["code"]
        current = float(qmap.loc[code, "close"]) if qmap is not None and code in qmap.index else p["buy_price"]
        cost_amount = p["buy_price"] * p["quantity"]
        market_value = current * p["quantity"]
        profit_pct = (current - p["buy_price"]) / p["buy_price"] * 100 if p["buy_price"] else 0.0
        sellable = engine._is_sellable(p["buy_date"], today)
        enriched.append({
            **p,
            "current_price": round(current, 2),
            "market_value": round(market_value, 2),
            "profit_amount": round(market_value - cost_amount, 2),
            "profit_pct": round(profit_pct, 2),
            "weight_pct": round(market_value / equity * 100, 2),
            "sellable_today": sellable,
            "t_plus_one_locked": engine.config.t_plus_one and not sellable,
        })

    initial = float(engine.state.get("initial_capital", engine.config.capital))
    closed = list(engine.state.get("closed_trades", []))
    closed.sort(key=lambda x: x.get("sell_date", ""), reverse=True)

    return {
        "has_data": True,
        "initial_capital": initial,
        "cash": summary["cash"],
        "market_value": summary["market_value"],
        "equity": summary["equity"],
        "total_return_pct": summary["total_return_pct"],
        "closed_count": summary["closed_count"],
        "trading_day_count": summary["trading_day_count"],
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


def get_portfolio_data(portfolio_stats: Optional[dict] = None, midterm: Optional[dict] = None) -> dict:
    if portfolio_stats is None:
        portfolio_stats = PortfolioManager().analyze()
    if midterm is None:
        midterm = _resolve_midterm(portfolio_stats)
    return _enrich_portfolio_with_midterm(portfolio_stats, midterm)


def get_sim_data() -> dict:
    return _enrich_sim_portfolio(SimReplayEngine())


def get_dashboard_data() -> dict:
    portfolio_stats = PortfolioManager().analyze()
    midterm = _resolve_midterm(portfolio_stats)
    sug = get_suggestions(portfolio_stats=portfolio_stats, midterm=midterm)
    return {
        "portfolio": get_portfolio_data(portfolio_stats=portfolio_stats, midterm=midterm),
        "sim": get_sim_data(),
        "ultra_short": load_cached_ultra_short(),
        "suggestions": sug["all"],
        "suggestion_groups": sug,
        "trades": get_trades_data(),
        "report": load_latest_report_meta(),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _run_quiet(func, *args, **kwargs) -> tuple[Any, str]:
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            result = func(*args, **kwargs)
        return result, buf.getvalue()
    except Exception:
        return None, buf.getvalue() + "\n" + traceback.format_exc()


@app.route("/")
def index():
    return render_template("dashboard.html", app_version=APP_VERSION)


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(get_dashboard_data())


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
    required = ("code", "name", "buy_date", "buy_price", "sell_date", "sell_price")
    missing = [k for k in required if not str(data.get(k, "")).strip()]
    if missing:
        return jsonify({"ok": False, "message": f"缺少字段: {', '.join(missing)}"}), 400

    try:
        journal = TradeJournal()
        record = journal.add_trade(
            code=str(data["code"]).zfill(6),
            name=str(data["name"]).strip(),
            buy_date=str(data["buy_date"])[:10],
            buy_price=float(data["buy_price"]),
            sell_date=str(data["sell_date"])[:10],
            sell_price=float(data["sell_price"]),
            quantity=int(data.get("quantity") or 100),
            strategy=str(data.get("strategy") or "手动"),
            note=str(data.get("note") or ""),
        )
        message = f"已录入 {record.name} 收益 {record.profit_pct:+.2f}%"
        sync_portfolio = data.get("sync_portfolio", True)
        if sync_portfolio not in (False, "false", 0, "0"):
            pm = PortfolioManager()
            trade_action = str(data.get("trade_action") or "sell").lower()
            if trade_action == "buy":
                ok, sync_msg = pm.apply_buy(
                    record.code,
                    record.name,
                    record.quantity,
                    record.buy_price,
                    buy_date=record.buy_date,
                    strategy=record.strategy,
                    note=record.note,
                )
            else:
                ok, sync_msg = pm.apply_sell(record.code, record.quantity)
            message += f"；{sync_msg}"

        return jsonify({
            "ok": True,
            "message": message,
            "trade": record.to_summary(),
            "data": get_dashboard_data(),
        })
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
        if data.get("total_capital") not in (None, ""):
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

    try:
        if action == "refresh":
            _, log = _run_quiet(collect_daily_market_close, verbose=True)
            message = "行情已刷新"
        elif action == "report":
            from daily_advisor import generate_daily_report

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
                run_midterm_advice, pm_stats, show_progress=False, full=True
            )
            rec_n = len(result.get("recommendations", [])) if isinstance(result, dict) else 0
            message = f"中线分析完成：复盘 {len(result.get('reviews', []))} 只，推荐 {rec_n} 只"
            extra["midterm"] = result
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
        elif action == "scan":
            from daily_advisor import run_ultra_short_scan

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
            "data": get_dashboard_data(),
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
