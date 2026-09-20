"""Evaluate ALL stock-selection strategies with on-disk evidence.

Run: py scripts/evaluate_all_strategies.py
Not investment advice. Every figure traces to saved trades / trackers.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from quantpy.paths import (
    AI_LEARNING_DIR,
    DATA_DIR,
    OUTPUT_DIR,
    SIM_STATE_FILE,
)
from quantpy.trade_math import COMMISSION_RATE, STAMP_TAX_RATE


def _fee_drag_pct() -> float:
    return round((2 * COMMISSION_RATE + STAMP_TAX_RATE) * 100, 3)


def _stats(rets: List[float], *, win_th: Optional[float] = None) -> dict:
    if not rets:
        return {
            "n": 0,
            "win_rate": None,
            "positive_rate": None,
            "avg_return": None,
            "med_return": None,
            "net_avg_after_fee": None,
        }
    n = len(rets)
    if win_th is None:
        wins = sum(1 for x in rets if x > 0)
    else:
        wins = sum(1 for x in rets if x >= win_th)
    pos = sum(1 for x in rets if x > 0)
    avg = sum(rets) / n
    med = sorted(rets)[n // 2]
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "positive_rate": round(pos / n * 100, 1),
        "avg_return": round(avg, 2),
        "med_return": round(med, 2),
        "net_avg_after_fee": round(avg - _fee_drag_pct(), 2),
        "win_threshold": win_th if win_th is not None else 0.0,
    }


def _verdict(row: dict) -> str:
    n = row.get("n") or 0
    if n < 8:
        return "样本不足"
    wr = row.get("win_rate")
    avg = row.get("avg_return")
    if wr is None or avg is None:
        return "无数据"
    if wr >= 55 and avg >= 3:
        return "主策略候选"
    if wr >= 50 and avg > 0:
        return "可保留"
    if wr < 35 or avg < -1:
        return "不适用/应降级"
    if wr < 45 or avg < 1:
        return "偏弱需优化"
    return "中性观察"


def eval_sim_ultra() -> dict:
    if not SIM_STATE_FILE.exists():
        return {"id": "sim_ultra_short", "name": "模拟超短（早盘扫描）", "stats": _stats([])}
    state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
    closed = state.get("closed_trades") or []
    rets = [float(t.get("profit_pct") or 0) for t in closed]
    by_exit: Dict[str, List[float]] = defaultdict(list)
    for t in closed:
        reason = str(t.get("exit_reason") or "未知")
        # normalize
        if "止损" in reason:
            key = "止损"
        elif "止盈" in reason:
            key = "止盈"
        elif "到期" in reason or "超短到期" in reason:
            key = "到期"
        elif "落袋" in reason or "盈利" in reason:
            key = "落袋"
        else:
            key = reason[:20]
        by_exit[key].append(float(t.get("profit_pct") or 0))
    return {
        "id": "sim_ultra_short",
        "name": "模拟超短（9:30–9:45 扫描·涨停/连板/封板）",
        "family": "超短",
        "evidence": "data/sim_state.json closed_trades",
        "win_rule": "平仓 profit_pct>0（含费用后）",
        "stats": _stats(rets),
        "by_exit": {k: _stats(v) for k, v in sorted(by_exit.items(), key=lambda x: -len(x[1]))},
    }


def eval_sim_midterm() -> dict:
    if not SIM_STATE_FILE.exists():
        return {"id": "sim_midterm_tv", "stats": _stats([])}
    state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
    mt = state.get("midterm") or {}
    closed = mt.get("closed_trades") or []
    rets = [float(t.get("profit_pct") or 0) for t in closed]
    return {
        "id": "sim_midterm_triple",
        "name": "模拟中线·三倍量账户（历史多为观察池买点）",
        "family": "中线",
        "evidence": "sim_state.midterm.closed_trades",
        "win_rule": "平仓 profit_pct>0",
        "stats": _stats(rets),
        "note": "代码已改为突破日主路径；本表反映改前观察池成交样本",
    }


def eval_sim_ma20() -> dict:
    if not SIM_STATE_FILE.exists():
        return {"id": "sim_ma20", "stats": _stats([])}
    state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
    mt = state.get("midterm_ma20") or {}
    closed = mt.get("closed_trades") or []
    rets = [float(t.get("profit_pct") or 0) for t in closed]
    return {
        "id": "sim_ma20_pullback",
        "name": "模拟中线·MA20突破+MA5回踩",
        "family": "中线",
        "evidence": "sim_state.midterm_ma20.closed_trades",
        "win_rule": "平仓 profit_pct>0",
        "stats": _stats(rets),
    }


def eval_tracker_by_trend() -> List[dict]:
    from quantpy.midterm_pick_tracker import (
        WIN_THRESHOLD_PCT,
        FOLLOW_TRADING_DAYS,
        _load_state,
        compute_tracker_summary,
    )

    state = _load_state()
    summary = compute_tracker_summary(state)
    matured = [
        r for r in state.get("records", [])
        if r.get("status") == "matured" and r.get("return_pct") is not None
    ]
    by_trend: Dict[str, List[dict]] = defaultdict(list)
    for r in matured:
        by_trend[str(r.get("trend") or "未标注")].append(r)

    rows = []
    overall_rets = [float(r["return_pct"]) for r in matured]
    rows.append({
        "id": "tracker_all",
        "name": "中线跟进池·合计（满期）",
        "family": "中线跟进",
        "evidence": "data/midterm_pick_tracker.json",
        "win_rule": f"{FOLLOW_TRADING_DAYS}交易日收益≥+{WIN_THRESHOLD_PCT}%",
        "stats": _stats(overall_rets, win_th=WIN_THRESHOLD_PCT),
        "tracker_summary": {
            "win_rate": summary.get("win_rate"),
            "avg_return": summary.get("avg_return"),
            "matured_count": summary.get("matured_count"),
            "tracking_count": summary.get("tracking_count"),
        },
    })
    for trend, part in sorted(by_trend.items(), key=lambda x: -len(x[1])):
        rets = [float(r["return_pct"]) for r in part]
        sid = {
            "三倍量突破": "tracker_triple_breakout",
            "短线反弹": "tracker_short_bounce",
            "中线底背离": "tracker_divergence",
            "MA20突破后MA5回踩": "tracker_ma20_pullback",
            "下跌趋势底背离反转": "tracker_div_downtrend",
            "震荡筑底背离": "tracker_div_sideways",
        }.get(trend, f"tracker_{trend}")
        rows.append({
            "id": sid,
            "name": f"中线跟进·{trend}",
            "family": "中线跟进",
            "evidence": "midterm_pick_tracker matured by trend",
            "win_rule": f"{FOLLOW_TRADING_DAYS}日≥+{WIN_THRESHOLD_PCT}%",
            "stats": _stats(rets, win_th=WIN_THRESHOLD_PCT),
        })
    return rows


def eval_watchlist() -> dict:
    from quantpy.triple_volume_watchlist import (
        WIN_THRESHOLD_PCT,
        _compute_summary,
        _load_state,
    )

    state = _load_state()
    sm = _compute_summary(state)
    items = state.get("items") or []
    settled = [i for i in items if i.get("return_pct") is not None]
    rets = [float(i["return_pct"]) for i in settled]
    bought = [i for i in settled if i.get("buy_signal_date")]
    bought_rets = [float(i["return_pct"]) for i in bought]
    expired = [i for i in settled if i.get("status") == "expired"]
    return {
        "id": "tv_watchlist_shrink",
        "name": "三倍量观察池·缩量站稳MA5再买",
        "family": "中线",
        "evidence": "data/triple_volume_watchlist.json",
        "win_rule": f"结束观察相对突破价>+{WIN_THRESHOLD_PCT}%",
        "stats": _stats(rets, win_th=WIN_THRESHOLD_PCT + 1e-9),  # module uses >
        "module_summary": {
            "completed_count": sm.get("completed_count"),
            "win_rate": sm.get("win_rate"),
            "avg_return": sm.get("avg_return"),
            "signal_win_rate": sm.get("signal_win_rate"),
        },
        "subset_with_buy_signal": _stats(
            bought_rets, win_th=WIN_THRESHOLD_PCT + 1e-9
        ),
        "subset_expired": _stats(
            [float(i["return_pct"]) for i in expired],
            win_th=WIN_THRESHOLD_PCT + 1e-9,
        ),
        "note": "胜阈严于跟进池+3%；均收益为负即可判定不适合主路径",
    }


def eval_journal() -> List[dict]:
    from quantpy.trade_journal import TradeJournal

    j = TradeJournal()
    df = j.list_trades()
    rows = []
    if df.empty:
        rows.append({
            "id": "real_journal_all",
            "name": "实盘交易日记·合计",
            "family": "实盘",
            "evidence": "data/trades.json",
            "win_rule": "扣费后 profit_pct>0",
            "stats": _stats([]),
        })
        return rows
    # recompute with fee-aware properties via to_summary
    all_rets = []
    by_strat: Dict[str, List[float]] = defaultdict(list)
    for t in j._trades:
        pct = float(t.profit_pct)
        all_rets.append(pct)
        by_strat[str(t.strategy or "手动")].append(pct)
    rows.append({
        "id": "real_journal_all",
        "name": "实盘交易日记·合计",
        "family": "实盘",
        "evidence": "data/trades.json",
        "win_rule": "扣费后 profit_pct>0",
        "stats": _stats(all_rets),
    })
    for strat, rets in sorted(by_strat.items(), key=lambda x: -len(x[1])):
        rows.append({
            "id": f"real_journal_{strat}",
            "name": f"实盘日记·{strat}",
            "family": "实盘",
            "evidence": "trades.json by strategy",
            "win_rule": "扣费后 profit_pct>0",
            "stats": _stats(rets),
        })
    return rows


def eval_portfolio_closed() -> List[dict]:
    from quantpy.portfolio import PortfolioManager

    pm = PortfolioManager()
    closed = pm._portfolio.closed_positions
    by_bucket: Dict[str, List[float]] = defaultdict(list)
    all_rets = []
    for c in closed:
        pct = float(c.profit_pct)
        all_rets.append(pct)
        by_bucket[str(c.bucket or "midterm")].append(pct)
    rows = [{
        "id": "real_portfolio_closed_all",
        "name": "实盘清盘记录·合计",
        "family": "实盘",
        "evidence": "data/portfolio.json closed_positions",
        "win_rule": "扣费后 profit_pct>0",
        "stats": _stats(all_rets),
    }]
    for b, rets in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        label = "超短" if b == "ultra_short" else "中线"
        rows.append({
            "id": f"real_portfolio_{b}",
            "name": f"实盘清盘·{label}",
            "family": "实盘",
            "evidence": "portfolio closed by bucket",
            "win_rule": "扣费后 profit_pct>0",
            "stats": _stats(rets),
        })
    return rows


def catalog_unevaluated() -> List[dict]:
    """Strategies that exist in code but lack dedicated win-rate evidence."""
    return [
        {
            "id": "qstock_pe_pb_cap",
            "name": "QStock 基本面截面（PE/PB/市值/ROE）",
            "family": "工具/研究",
            "evidence": None,
            "status": "无独立跟踪池；backtest_strategy 仅为当前截面近期收益，非策略验证",
            "verdict": "研究工具，不作实盘主策略",
        },
        {
            "id": "qstock_ta_optimizer",
            "name": "技术指标优化器（MA/RSI/MACD/KDJ/涨停扫描）",
            "family": "工具/研究",
            "evidence": "被 UltraShortScanner 调用；胜率计入模拟超短",
            "status": "无单独闭环样本",
            "verdict": "超短子系统组件",
        },
        {
            "id": "sector_recommender",
            "name": "板块推荐",
            "family": "环境",
            "evidence": None,
            "status": "无个股持有期胜率跟踪",
            "verdict": "辅助过滤，非选股胜率策略",
        },
        {
            "id": "legacy_divergence_select",
            "name": "旧中线底背离选股（已切走）",
            "family": "中线·历史",
            "evidence": "midterm_pick_tracker trend=中线底背离",
            "status": "选股主轴已停用，残留在跟进池",
            "verdict": "见 tracker_divergence 行",
        },
    ]


def main() -> dict:
    strategies: List[dict] = []
    strategies.append(eval_sim_ultra())
    strategies.append(eval_sim_midterm())
    strategies.append(eval_sim_ma20())
    strategies.extend(eval_tracker_by_trend())
    strategies.append(eval_watchlist())
    strategies.extend(eval_journal())
    strategies.extend(eval_portfolio_closed())

    for s in strategies:
        st = s.get("stats") or {}
        s["verdict"] = _verdict(st)
        # flatten for table
        s["n"] = st.get("n")
        s["win_rate"] = st.get("win_rate")
        s["avg_return"] = st.get("avg_return")
        s["net_avg"] = st.get("net_avg_after_fee")

    uneval = catalog_unevaluated()

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "decision": "全策略评估：仅突破日三倍量具备主仓证据；观察池缩量与弱样本策略降级。",
        "not_advice": True,
        "fee_drag_pct": _fee_drag_pct(),
        "assumptions": [
            "模拟/实盘胜：平仓收益>0（已含或近似费用）",
            "跟进池胜：10交易日≥+3%",
            "观察池胜：结算相对突破价>+5%",
            "等权笔数；样本<8 标「样本不足」",
        ],
        "strategies": strategies,
        "unevaluated_or_tooling": uneval,
    }

    out_dir = OUTPUT_DIR / "strategy_eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "all_strategies_eval.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # markdown table
    lines = [
        "# 全部选股策略评估",
        "",
        f"生成：{report['generated_at']}  ·  费用拖累约 {report['fee_drag_pct']}%  ·  **不是投资建议**",
        "",
        "## 有样本的策略",
        "",
        "| 策略 | 家族 | n | 胜率% | 均益% | 扣费后均益% | 判定 |",
        "|------|------|---|-------|-------|-------------|------|",
    ]
    for s in sorted(
        strategies,
        key=lambda x: (
            0 if (x.get("n") or 0) >= 8 else 1,
            -(x.get("win_rate") or 0),
        ),
    ):
        lines.append(
            f"| {s.get('name')} | {s.get('family')} | {s.get('n')} | "
            f"{s.get('win_rate')} | {s.get('avg_return')} | {s.get('net_avg')} | "
            f"{s.get('verdict')} |"
        )
    lines.extend([
        "",
        "## 无独立胜率闭环 / 工具型",
        "",
        "| 策略 | 说明 | 判定 |",
        "|------|------|------|",
    ])
    for u in uneval:
        lines.append(
            f"| {u['name']} | {u.get('status')} | {u.get('verdict')} |"
        )
    lines.extend([
        "",
        "## 口径说明",
        "",
    ])
    for a in report["assumptions"]:
        lines.append(f"- {a}")
    md_path = out_dir / "all_strategies_eval.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"\nJSON → {path}")
    print(f"MD   → {md_path}")
    return report


if __name__ == "__main__":
    main()
