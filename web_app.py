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
from typing import Any

import pandas as pd
from flask import Flask, jsonify, render_template, request

from portfolio import PortfolioManager
from sim_replay import SimReplayEngine
from stock_data import collect_daily_market_close, get_realtime_quotes
from trade_journal import TradeJournal

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
REPORT_DIR = OUTPUT_DIR / "daily_reports"
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


def _ultra_short_records(df: pd.DataFrame, top_n: int = 10) -> list[dict]:
    if df is None or df.empty:
        return []
    cols = ["code", "name", "ultra_short_score", "pct_chg", "turnover", "consecutive_boards", "tags"]
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


def get_suggestions() -> list[str]:
    suggestions: list[str] = []
    pm = PortfolioManager()
    if pm.list_positions():
        suggestions.extend(pm.generate_suggestions())
    journal = TradeJournal()
    suggestions.extend(journal.generate_suggestions(days=30))
    return suggestions


def _enrich_sim_portfolio(engine: SimReplayEngine) -> dict:
    summary = engine.get_summary()
    equity = summary["equity"] or 1.0
    positions = summary.get("positions", [])
    quotes = get_realtime_quotes([p["code"] for p in positions]) if positions else None
    qmap = quotes.set_index("code") if quotes is not None and not quotes.empty else None

    enriched = []
    for p in positions:
        code = p["code"]
        current = float(qmap.loc[code, "close"]) if qmap is not None and code in qmap.index else p["buy_price"]
        cost_amount = p["buy_price"] * p["quantity"]
        market_value = current * p["quantity"]
        profit_pct = (current - p["buy_price"]) / p["buy_price"] * 100 if p["buy_price"] else 0.0
        enriched.append({
            **p,
            "current_price": round(current, 2),
            "market_value": round(market_value, 2),
            "profit_amount": round(market_value - cost_amount, 2),
            "profit_pct": round(profit_pct, 2),
            "weight_pct": round(market_value / equity * 100, 2),
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
            "stop_loss_pct": engine.config.stop_loss_pct,
            "take_profit_pct": engine.config.take_profit_pct,
            "max_hold_days": engine.config.max_hold_days,
            "min_score": engine.config.min_score,
            "max_positions": engine.config.max_positions,
        },
        "updated_at": engine.state.get("updated_at", ""),
    }


def get_portfolio_data() -> dict:
    return PortfolioManager().analyze()


def get_sim_data() -> dict:
    return _enrich_sim_portfolio(SimReplayEngine())


def get_dashboard_data() -> dict:
    return {
        "portfolio": get_portfolio_data(),
        "sim": get_sim_data(),
        "ultra_short": load_cached_ultra_short(),
        "suggestions": get_suggestions(),
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
    return render_template("dashboard.html")


@app.route("/api/dashboard")
def api_dashboard():
    return jsonify(get_dashboard_data())


@app.route("/api/report/latest")
def api_report_latest():
    return jsonify(load_latest_report_content())


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
            message = "日报已生成"
            extra["report"] = load_latest_report_meta()
            if path:
                extra["report"]["path"] = str(path).replace("\\", "/")
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
                }
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
