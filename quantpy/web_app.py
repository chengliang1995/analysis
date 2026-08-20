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
import json
import traceback
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from flask import Flask, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider

from quantpy.json_util import sanitize_for_json

from quantpy import __version__ as APP_VERSION
from quantpy.paths import LOG_DIR, OUTPUT_DIR, PROJECT_ROOT, REPORT_DIR, RETENTION_DAYS, STATIC_DIR, TEMPLATES_DIR
from quantpy.portfolio import PortfolioManager
from quantpy.retention import prune_retention_files
from quantpy.sim_replay import SimReplayEngine
from quantpy.sim_midterm import enrich_midterm_sim, run_sim_midterm_select
from quantpy.ai_learning_optimizer import run_ai_learning
from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor
from quantpy.midterm_level_alerts import scan_midterm_level_alerts
from quantpy.stock_data import (
    fetch_board_list,
    get_instrument_index,
    get_realtime_quotes,
    get_stock_recent_bars,
    is_etf_code,
    lookup_instrument_by_code,
    lookup_instrument_by_name,
    price_step_for_code,
)
from quantpy.sector_recommender import run_sector_recommendations
from quantpy.real_portfolio_reviewer import (
    load_latest_real_review,
    run_real_portfolio_review,
)
from quantpy.stock_pnl_history import get_stock_pnl_history
from quantpy.trade_journal import TradeJournal
from quantpy.web_dashboard import (
    get_dashboard_data,
    get_sim_data,
    get_trades_data,
    load_latest_report_content,
    load_latest_report_meta,
    refresh_holdings_quotes,
)

BASE_DIR = PROJECT_ROOT


class SafeJSONProvider(DefaultJSONProvider):
    """API 响应中禁止 NaN/Inf（浏览器 JSON.parse 无法解析）。"""

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("default", self.default)
        return json.dumps(sanitize_for_json(obj), **kwargs)


app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)
app.json = SafeJSONProvider(app)
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.after_request
def _no_cache(response):
    if request.path == "/" or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


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


@app.route("/api/stock/industries")
def api_stock_industries():
    from quantpy.stock_data import list_industries_from_map

    return jsonify({"ok": True, "items": list_industries_from_map()})


@app.route("/api/sector/boards")
def api_sector_boards():
    board_type = str(request.args.get("type") or "concept").strip().lower()
    if board_type not in ("concept", "industry"):
        board_type = "concept"
    try:
        items = fetch_board_list(board_type, force_refresh=False)
        return jsonify({"ok": True, "board_type": board_type, "items": items})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "items": []}), 500


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
    pnl = get_stock_pnl_history(code, include_current=True)
    return jsonify(
        {
            "ok": True,
            "code": code,
            "name": name or pnl.get("name", ""),
            "days": len(bars),
            "bars": bars,
            "summary": {
                "latest_close": closes[-1],
                "period_high": max(highs),
                "period_low": min(lows),
                "period_change_pct": round((closes[-1] - first_close) / first_close * 100, 2),
            },
            "pnl_history": pnl,
        }
    )


@app.route("/api/stock/<code>/midterm")
def api_stock_midterm(code: str):
    """个股中线技术面分析（结合持仓成本/仓位）。"""
    code = str(code).zfill(6)
    name = str(request.args.get("name") or "").strip()
    cost = request.args.get("cost", type=float)
    weight = request.args.get("weight", type=float)

    pm = PortfolioManager()
    stats = pm.analyze()
    pos = next(
        (p for p in stats.get("positions", []) if str(p.get("code")).zfill(6) == code),
        None,
    )
    if pos:
        name = name or pos.get("name", "")
        cost = float(pos.get("cost_price", 0)) if cost is None else cost
        weight = float(pos.get("weight_pct", 0)) if weight is None else weight
    else:
        cost = cost or 0.0
        weight = weight or 0.0

    advisor = MidtermPortfolioAdvisor()
    analysis = advisor.analyze_stock(
        code=code,
        name=name,
        cost_price=cost or 0,
        weight_pct=weight or 0,
    )

    rec_match = None
    midterm = _resolve_midterm(stats)
    for r in midterm.get("recommendations") or []:
        if str(r.get("code")).zfill(6) == code:
            rec_match = r
            break

    held = pos is not None and (
        pos.get("bucket") == "midterm"
        or pos.get("bucket_label") == "中线"
    )

    return jsonify({
        "ok": analysis.get("ok", False),
        "code": code,
        "name": analysis.get("name") or name or code,
        "held": held,
        "analysis": analysis,
        "recommendation": rec_match,
        "message": analysis.get("message", ""),
    })


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
        if trade_action not in ("buy", "sell", "t"):
            trade_action = "sell"

        sell_price_raw = str(data.get("sell_price", "")).strip()
        sell_date_raw = str(data.get("sell_date", "")).strip()
        has_sell = bool(sell_price_raw)

        if trade_action in ("sell", "t") and not has_sell:
            label = "做T" if trade_action == "t" else "卖出"
            return jsonify({"ok": False, "message": f"{label}时需填写卖出价"}), 400
        if trade_action == "buy" and has_sell:
            return jsonify({"ok": False, "message": "买入类型无需填写卖出价"}), 400
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
            if trade_action == "t":
                message = f"已录入做T {record.name} 收益 {record.profit_pct:+.2f}%"
            else:
                message = f"已录入卖出 {record.name} 收益 {record.profit_pct:+.2f}%"
        elif trade_action == "buy":
            message = f"已记录买入 {name}({code})，未写入交易日记（未平仓）"
        else:
            return jsonify({
                "ok": False,
                "message": "请填写卖出价，或选择买入类型",
            }), 400

        if sync_portfolio and trade_action != "t":
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
            from quantpy.orchestration import run_action_report

            result, log = _run_quiet(
                run_action_report,
                top_prefilter=200,
                min_score=35,
                days=30,
                include_watchlist=True,
                action="report",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                return jsonify({
                    "ok": False,
                    "message": (result or {}).get("message") or "日报生成失败，请查看日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            message = result.get("message") or "日报已生成"
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
            from quantpy.orchestration import run_action_midterm

            industry = str(request.args.get("industry") or "").strip() or None
            performance = str(request.args.get("performance") or "").strip() or None
            result, log = _run_quiet(
                run_action_midterm,
                full=True,
                industry=industry,
                performance=performance,
                apply_to_sim=True,
                show_progress=True,
                action="midterm",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                return jsonify({
                    "ok": False,
                    "message": (result or {}).get("message") or "中线分析失败，请查看运行日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500 if (result or {}).get("message") != "暂无实盘持仓" else 400
            message = result.get("message") or "中线分析完成"
            payload = result.get("payload") or {}
            midterm_result = payload.get("midterm") or {}
            alerts = payload.get("level_alerts") or {}
            pm_stats = payload.get("portfolio_stats")
            if payload.get("sim_midterm"):
                extra["sim_midterm"] = payload["sim_midterm"]
            extra["midterm"] = midterm_result
            extra["level_alerts"] = alerts
            extra["midterm_content"] = payload.get("midterm_content") or {
                "name": "实盘中线分析报告",
                "content": midterm_result.get("markdown") or "",
            }
            prefetched_dashboard = get_dashboard_data(
                portfolio_stats=pm_stats,
                midterm=midterm_result,
            )
            prefetched_dashboard["level_alerts"] = alerts
        elif action == "midterm-track":
            from quantpy.orchestration import run_action_midterm_track

            result, log = _run_quiet(
                run_action_midterm_track,
                show_progress=True,
                action="midterm-track",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                return jsonify({
                    "ok": False,
                    "message": (result or {}).get("message") or "中线跟进失败",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            message = result.get("message") or ""
            payload = result.get("payload") or {}
            if payload.get("selection_tuning"):
                extra["selection_tuning"] = payload["selection_tuning"]
            extra["midterm_tracker"] = payload.get("midterm_tracker")
        elif action == "midterm-triple-volume":
            from quantpy.orchestration import run_action_triple_volume

            result, log = _run_quiet(
                run_action_triple_volume,
                force=force,
                show_progress=True,
                action="midterm-triple-volume",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                return jsonify({
                    "ok": False,
                    "message": (result or {}).get("message") or "三倍量选股失败，请查看运行日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            message = result.get("message") or ""
            payload = result.get("payload") or {}
            tv = payload.get("triple_volume") or {}
            extra["triple_volume"] = tv
            extra["triple_volume_content"] = {
                "name": "三倍量选股报告",
                "content": tv.get("markdown") or "",
            }
            if tv.get("watchlist"):
                extra["triple_volume_watchlist"] = tv["watchlist"]
        elif action == "triple-volume-watch":
            from quantpy.orchestration import run_action_triple_watch

            result, log = _run_quiet(
                run_action_triple_watch,
                show_progress=True,
                action="triple-volume-watch",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                return jsonify({
                    "ok": False,
                    "message": (result or {}).get("message") or "观察池评估失败",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            message = result.get("message") or ""
            payload = result.get("payload") or {}
            extra["triple_volume_watchlist"] = payload.get("triple_volume_watchlist")
            extra["watch_eval"] = payload.get("watch_eval")
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
        elif action == "sim-midterm-select":
            force = str(request.args.get("force") or "").lower() in ("1", "true", "yes")
            use_cache = str(request.args.get("cache") or "").lower() in ("1", "true", "yes")
            industry = str(request.args.get("industry") or "").strip() or None
            performance = str(request.args.get("performance") or "").strip() or None
            engine = SimReplayEngine()
            engine.reload_state()
            result, log = _run_quiet(
                run_sim_midterm_select,
                engine,
                show_progress=True,
                force=force,
                industry=industry,
                performance=performance,
                use_cache=use_cache,
                action="sim-midterm-select",
            )
            if result is None:
                return jsonify({
                    "ok": False,
                    "message": "模拟中线选股失败，请查看运行日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            if isinstance(result, dict) and result.get("ok"):
                message = result.get("message") or "模拟中线选股完成"
            elif isinstance(result, dict):
                message = result.get("message") or "无推荐标的"
            else:
                message = "模拟中线选股失败"
            extra["sim_midterm"] = result
            if isinstance(result, dict) and not result.get("ok"):
                payload = {
                    "ok": False,
                    "message": message,
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                    **extra,
                }
                return jsonify(payload), 200
        elif action == "sim-midterm":
            from quantpy.sim_midterm import (
                check_midterm_exits,
                run_midterm_sim_review,
            )

            engine = SimReplayEngine()
            engine.reload_state()
            def _sim_midterm_run():
                check_midterm_exits(engine, show_progress=True)
                reviews = run_midterm_sim_review(engine, show_progress=True)
                return {"reviews": reviews, "summary": enrich_midterm_sim(engine.state)}

            result, log = _run_quiet(_sim_midterm_run, action="sim-midterm")
            n = len(result.get("reviews", [])) if isinstance(result, dict) else 0
            message = f"模拟中线复盘完成：{n} 只持仓"
            extra["sim_midterm"] = result
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
            from quantpy.orchestration import run_action_ultra_scan

            result, log = _run_quiet(
                run_action_ultra_scan,
                top_prefilter=200,
                min_score=35,
                action="scan",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                return jsonify({
                    "ok": False,
                    "message": (result or {}).get("message") or "超短扫描失败",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            message = result.get("message") or ""
            payload = result.get("payload") or {}
            extra["ultra_short"] = payload.get("ultra_short") or []
        elif action == "sector":
            board_type = str(request.args.get("type") or "concept").strip().lower()
            if board_type not in ("concept", "industry"):
                board_type = "concept"
            board_code = str(request.args.get("board") or "").strip().upper() or None
            result, log = _run_quiet(
                run_sector_recommendations,
                board_type=board_type,
                board_code=board_code,
                top_boards=8,
                stocks_per_board=5,
                show_progress=True,
                action="sector",
            )
            if not isinstance(result, dict) or not result.get("ok"):
                msg = (result or {}).get("message") if isinstance(result, dict) else "板块推荐失败"
                return jsonify({
                    "ok": False,
                    "message": msg or "板块推荐失败，请查看运行日志",
                    "log": log.strip(),
                    "data": get_dashboard_data(),
                }), 500
            stats = result.get("stats") or {}
            label = result.get("board_type_label") or "板块"
            message = (
                f"{label}推荐完成：{stats.get('board_count', 0)} 个板块 · "
                f"{stats.get('stock_count', 0)} 只标的"
            )
            extra["sector"] = result
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
        if extra.get("sector") is not None:
            payload["data"]["sector"] = extra["sector"]
        if extra.get("midterm_tracker") is not None:
            payload["data"]["midterm_tracker"] = extra["midterm_tracker"]
        if extra.get("level_alerts") is not None:
            payload["data"]["level_alerts"] = extra["level_alerts"]
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
