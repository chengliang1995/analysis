"""Evaluate midterm selection strategies from on-disk tracker / watchlist.

Run: py -m scripts.evaluate_midterm_strategies
Not investment advice. Win = return >= threshold over follow window.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from quantpy.midterm_pick_tracker import (
    FOLLOW_TRADING_DAYS,
    WIN_THRESHOLD_PCT,
    _group_factor_stats,
    _group_score_stats,
    _group_tag_stats,
    _load_state as load_tracker_state,
    compute_interim_summary,
    compute_tracker_summary,
    load_tracker_summary,
)
from quantpy.paths import DATA_DIR, OUTPUT_DIR
from quantpy.triple_volume_watchlist import (
    WIN_THRESHOLD_PCT as TV_WIN_TH,
    _compute_summary as tv_summary,
    _load_state as load_tv_state,
    load_watchlist_summary,
)
from quantpy.trade_math import COMMISSION_RATE, STAMP_TAX_RATE


def _pct(n: float, d: float) -> float:
    return round(n / d * 100, 1) if d else 0.0


def _fee_drag_round_trip() -> float:
    """Approx round-trip fee as percent of notional (no slippage)."""
    return (2 * COMMISSION_RATE + STAMP_TAX_RATE) * 100


def _bucket_returns(records: List[dict], key_fn) -> List[dict]:
    buckets: Dict[str, List[float]] = defaultdict(list)
    wins: Dict[str, int] = defaultdict(int)
    for r in records:
        k = key_fn(r)
        if k is None:
            continue
        ret = r.get("return_pct")
        if ret is None:
            continue
        buckets[str(k)].append(float(ret))
        if r.get("is_win"):
            wins[str(k)] += 1
    rows = []
    for k, rets in buckets.items():
        rows.append({
            "key": k,
            "count": len(rets),
            "win_rate": _pct(wins[k], len(rets)),
            "avg_return": round(sum(rets) / len(rets), 2),
            "med_return": round(float(pd.Series(rets).median()), 2),
        })
    rows.sort(key=lambda x: (-x["count"], -x["win_rate"]))
    return rows


def evaluate_midterm_tracker(*, refresh: bool = False) -> dict:
    if refresh:
        summary = load_tracker_summary(evaluate=True)
    else:
        state = load_tracker_state()
        summary = compute_tracker_summary(state)
        interim = compute_interim_summary(state)
        summary["interim"] = interim
        summary["factor_stats"] = _group_factor_stats(
            [r for r in state.get("records", []) if r.get("status") == "matured"],
            "conditions_hit",
        ) if False else summary
        matured = [r for r in state.get("records", []) if r.get("status") == "matured"
                   and r.get("return_pct") is not None]
        # rebuild factor/tag/score from matured
        from quantpy.midterm_pick_tracker import (
            _group_factor_stats as gfs,
            _group_tag_stats as gts,
            _group_score_stats as gss,
            _group_key_stats as gks,
        )
        # conditions_hit is list field — use existing helpers after enrich
        summary["by_score"] = gss(matured)
        summary["by_tag"] = gts(matured)
        # factor: flatten conditions
        factor_buckets: Dict[str, List[dict]] = defaultdict(list)
        for r in matured:
            for c in (r.get("conditions_hit") or r.get("factors") or []):
                factor_buckets[str(c)].append(r)
            # also try tags as factors
        from quantpy.midterm_pick_tracker import _stats_from_buckets
        summary["by_factor"] = _stats_from_buckets(factor_buckets)
        ma60 = []
        for r in matured:
            # ma60_slope or similar
            slope = r.get("ma60_slope") or r.get("ma60_trend")
            if slope is not None:
                ma60.append(r)
        summary["by_ma60"] = gks(matured, "ma60_trend") if matured and "ma60_trend" in matured[0] else []
        summary["matured_records"] = matured
        summary["n_matured"] = len(matured)
        summary["n_tracking"] = sum(1 for r in state.get("records", []) if r.get("status") == "tracking")
    return summary


def evaluate_triple_volume(*, refresh: bool = False) -> dict:
    if refresh:
        return load_watchlist_summary(evaluate=True)
    state = load_tv_state()
    summary = tv_summary(state)
    items = state.get("items", [])
    settled = [
        i for i in items
        if i.get("return_pct") is not None and i.get("status") in ("completed", "expired", "bought", "settled")
        or (i.get("return_pct") is not None and i.get("is_win") is not None)
    ]
    # broader: any with return and is_win set
    settled = [i for i in items if i.get("return_pct") is not None]
    buy_signal = [i for i in settled if i.get("status") == "buy_signal" or i.get("had_buy_signal")
                  or "买入" in str(i.get("settle_reason", ""))
                  or i.get("buy_signal_date")]
    # status-based from module summary
    summary["settled_items"] = settled
    summary["n_settled"] = len(settled)
    summary["by_status"] = _bucket_returns(settled, lambda r: r.get("status") or "unknown")
    return summary


def _print_table(rows: List[dict], cols: List[str], limit: int = 15) -> None:
    if not rows:
        print("  (无样本)")
        return
    df = pd.DataFrame(rows)
    use = [c for c in cols if c in df.columns]
    print(df[use].head(limit).to_string(index=False))


def main(refresh: bool = False) -> dict:
    fee_pct = _fee_drag_round_trip()
    print("=" * 64)
    print("中线策略评估（可重跑）")
    print(f"费用拖累粗算(双边佣金+印花税，无滑点): {fee_pct:.3f}%")
    print("不是投资建议。")
    print("=" * 64)

    mid = evaluate_midterm_tracker(refresh=refresh)
    print("\n## 1) 中线跟进池 midterm_pick_tracker")
    print(
        f"满期样本 {mid.get('matured_count', mid.get('n_matured', '?'))} | "
        f"跟踪中 {mid.get('tracking_count', mid.get('n_tracking', '?'))} | "
        f"窗口 {mid.get('follow_trading_days', FOLLOW_TRADING_DAYS)} 交易日 | "
        f"胜阈 ≥{mid.get('win_threshold_pct', WIN_THRESHOLD_PCT)}%"
    )
    print(
        f"胜率 {mid.get('win_rate')}% | 均收益 {mid.get('avg_return')}% | "
        f"中位 {mid.get('median_return', mid.get('med_return', 'n/a'))}"
    )
    interim = mid.get("interim") or {}
    if interim:
        print(
            f"未满期中间态: n={interim.get('count', interim.get('sample_count'))} "
            f"胜率 {interim.get('win_rate')}% 均益 {interim.get('avg_return')}%"
        )

    matured = mid.get("matured_records") or []
    if not matured:
        state = load_tracker_state()
        matured = [
            r for r in state.get("records", [])
            if r.get("status") == "matured" and r.get("return_pct") is not None
        ]
        mid["by_score"] = _group_score_stats(matured)
        mid["by_tag"] = _group_tag_stats(matured)

    print("\n按评分档:")
    _print_table(mid.get("by_score") or _group_score_stats(matured),
                 ["key", "count", "win_rate", "avg_return"])
    print("\n按标签(Top):")
    _print_table(mid.get("by_tag") or _group_tag_stats(matured),
                 ["key", "count", "win_rate", "avg_return"])

    # distance to MA20 at pick if present
    if matured and any(r.get("dist_ma20_pct") is not None for r in matured):
        print("\n按相对MA20距离:")
        def ma20_bucket(r):
            d = r.get("dist_ma20_pct")
            if d is None:
                return None
            d = float(d)
            if d > 8:
                return ">8%追高"
            if d > 3:
                return "3-8%"
            if d >= -2:
                return "贴MA20(-2~3%)"
            return "跌破MA20"
        _print_table(_bucket_returns(matured, ma20_bucket),
                     ["key", "count", "win_rate", "avg_return", "med_return"])

    print("\n## 2) 三倍量观察池")
    tv = evaluate_triple_volume(refresh=refresh)
    print(
        f"已结算 {tv.get('completed_count', tv.get('n_settled'))} | "
        f"胜阈 >{tv.get('win_threshold_pct', TV_WIN_TH)}% | "
        f"胜率 {tv.get('win_rate')}% | 均益 {tv.get('avg_return')}% | "
        f"买点信号胜率 {tv.get('signal_win_rate')}%"
    )
    print("按状态:")
    _print_table(tv.get("by_status") or [],
                 ["key", "count", "win_rate", "avg_return", "med_return"])

    # net of fees heuristic
    def net_edge(avg_ret: Optional[float]) -> Optional[float]:
        if avg_ret is None:
            return None
        return round(float(avg_ret) - fee_pct, 2)

    report = {
        "fee_drag_pct": fee_pct,
        "midterm_tracker": {
            "win_rate": mid.get("win_rate"),
            "avg_return": mid.get("avg_return"),
            "net_avg_return": net_edge(mid.get("avg_return")),
            "matured_count": mid.get("matured_count", len(matured)),
            "win_threshold_pct": mid.get("win_threshold_pct", WIN_THRESHOLD_PCT),
            "follow_trading_days": mid.get("follow_trading_days", FOLLOW_TRADING_DAYS),
            "by_score": mid.get("by_score") or _group_score_stats(matured),
            "by_tag": (mid.get("by_tag") or _group_tag_stats(matured))[:20],
        },
        "triple_volume": {
            "win_rate": tv.get("win_rate"),
            "avg_return": tv.get("avg_return"),
            "net_avg_return": net_edge(tv.get("avg_return")),
            "signal_win_rate": tv.get("signal_win_rate"),
            "completed_count": tv.get("completed_count", tv.get("n_settled")),
            "win_threshold_pct": tv.get("win_threshold_pct", TV_WIN_TH),
            "by_status": tv.get("by_status"),
        },
        "assumptions": [
            "中线跟进胜: 满期收益≥+3%（约10交易日）",
            "三倍量胜: 结束观察相对突破价>+5%",
            "费用: 佣金万2.5双边+印花税万5，无滑点",
            "等权笔数胜率，非金额加权",
        ],
    }

    out = OUTPUT_DIR / "strategy_eval"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "midterm_strategy_eval.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {path}")
    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--refresh", action="store_true", help="重新拉行情结算跟踪池")
    args = p.parse_args()
    main(refresh=args.refresh)
