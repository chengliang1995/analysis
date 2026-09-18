"""
统一动作编排：Web / CLI / 定时任务共用入口。

返回结构约定::
    {
      "ok": bool,
      "message": str,
      "payload": dict,      # 业务结果
      "artifacts": list,    # 落盘路径等
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


def _result(
    ok: bool,
    message: str,
    payload: Optional[dict] = None,
    artifacts: Optional[List[str]] = None,
) -> dict:
    return {
        "ok": bool(ok),
        "message": str(message or ""),
        "payload": payload or {},
        "artifacts": list(artifacts or []),
    }


def run_action_ultra_scan(
    *,
    top_prefilter: int = 200,
    min_score: int = 35,
    top_n: int = 10,
) -> dict:
    from datetime import datetime

    from quantpy.daily_advisor import run_ultra_short_scan
    from quantpy.json_util import df_to_records_safe
    from quantpy.paths import OUTPUT_DIR

    df = run_ultra_short_scan(top_prefilter=top_prefilter, min_score=min_score)
    if df is None:
        df = pd.DataFrame()
    count = len(df) if not df.empty else 0
    # Web 侧展示 TOP N；完整 CSV 仍由扫描函数落盘
    records = []
    if count:
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
        records = df_to_records_safe(rows)
    artifacts: List[str] = []
    today = OUTPUT_DIR / f"ultra_short_{datetime.now().strftime('%Y%m%d')}.csv"
    if today.exists():
        artifacts.append(str(today))
    return _result(
        True,
        f"超短扫描完成，命中 {count} 只",
        payload={"ultra_short": records, "count": count},
        artifacts=artifacts,
    )


def run_action_triple_volume(
    *,
    force: bool = False,
    show_progress: bool = True,
    exclude_held: bool = True,
) -> dict:
    from quantpy.midterm_triple_volume_selector import run_triple_volume_select
    from quantpy.portfolio import PortfolioManager

    held: List[str] = []
    if exclude_held:
        held = [str(p.code).zfill(6) for p in PortfolioManager().list_positions()]
    result = run_triple_volume_select(
        exclude_codes=held,
        show_progress=show_progress,
        force=force,
    )
    if not isinstance(result, dict):
        return _result(False, "三倍量选股失败")
    rec_n = len(result.get("recommendations") or [])
    select_stats = result.get("select_stats") or {}
    wl_added = 0
    if isinstance(result.get("watchlist"), dict):
        wl_added = int((result["watchlist"].get("record") or {}).get("added") or 0)
    message = (
        f"三倍量选股完成：命中 {rec_n} 只"
        f"（量比{select_stats.get('vol_prefilter_count', select_stats.get('prefilter_count', 0))}"
        f"→K线{select_stats.get('hist_scan_count', select_stats.get('prefilter_count', 0))}"
        f"→命中{select_stats.get('scored_pass', 0)}）"
    )
    if wl_added:
        message += f"；观察池新增 {wl_added} 只"
    artifacts = []
    if result.get("report_path"):
        artifacts.append(str(result["report_path"]))
    return _result(True, message, payload={"triple_volume": result}, artifacts=artifacts)


def run_action_triple_watch(*, show_progress: bool = True) -> dict:
    from quantpy.triple_volume_watchlist import (
        load_watchlist_summary,
        sync_and_evaluate_watchlist,
    )

    result = sync_and_evaluate_watchlist(show_progress=show_progress)
    wl = result if isinstance(result, dict) and "summary" in result else load_watchlist_summary(
        evaluate=False,
    )
    eval_part = (result or {}).get("eval") if isinstance(result, dict) else {}
    buy_n = int((eval_part or {}).get("new_buy_signals") or 0)
    added_n = int((result or {}).get("record", {}).get("added") or 0) if isinstance(result, dict) else 0
    summary = wl.get("summary") or {}
    message = (
        f"观察池评估完成：同步入池 {added_n} 只，新增买入提示 {buy_n} 条"
        f"（观察中 {summary.get('watching_count', 0)} · "
        f"买入信号 {summary.get('buy_signal_count', 0)} · "
        f"已结束 {summary.get('completed_count', 0)} · "
        f"胜率 {summary.get('win_rate', 0)}%）"
    )
    return _result(
        True,
        message,
        payload={
            "triple_volume_watchlist": wl,
            "watch_eval": eval_part or result,
        },
    )


def run_action_midterm_track(*, show_progress: bool = True) -> dict:
    from quantpy.midterm_pick_tracker import (
        derive_factor_tuning,
        run_midterm_tracker_cycle,
    )
    from quantpy.selection_tuning import build_selection_tuning, format_tuning_summary

    result = run_midterm_tracker_cycle(None, show_progress=show_progress)
    summary = (result or {}).get("summary") or {}
    message = (
        f"中线跟进更新：跟踪 {summary.get('tracking_count', 0)} 只，"
        f"成熟 {summary.get('matured_count', 0)} 只，"
        f"胜率 {summary.get('win_rate', 0)}%"
    )
    selection_tuning = None
    try:
        factor = derive_factor_tuning(summary, summary.get("interim"))
        tuning = build_selection_tuning()
        notes = (factor.get("notes") or [])[:3]
        if notes:
            message += "；策略已按跟进调优：" + "；".join(notes)
        else:
            message += f"；{format_tuning_summary(tuning).splitlines()[0]}"
        selection_tuning = {
            "midterm_min_score": tuning.midterm_min_score,
            "condition_bonus": tuning.midterm_condition_bonus,
            "condition_penalty": tuning.midterm_condition_penalty,
            "tag_bonus": tuning.midterm_tag_bonus,
            "tag_penalty": tuning.midterm_tag_penalty,
            "notes": tuning.notes[:6],
        }
    except Exception:
        pass
    payload = {"midterm_tracker": result}
    if selection_tuning:
        payload["selection_tuning"] = selection_tuning
    return _result(True, message, payload=payload)


def run_action_report(
    *,
    top_prefilter: int = 200,
    min_score: int = 35,
    days: int = 30,
    include_watchlist: bool = True,
) -> dict:
    """生成日报。include_watchlist=False 时跳过观察池（避免与独立相位双跑）。"""
    from quantpy.daily_advisor import generate_daily_report

    path = generate_daily_report(
        days=days,
        top_prefilter=top_prefilter,
        min_score=min_score,
        include_watchlist=include_watchlist,
    )
    if path is None:
        return _result(False, "日报生成失败，请查看日志")
    return _result(
        True,
        "日报已生成",
        payload={"report_path": str(path)},
        artifacts=[str(path)],
    )


def run_action_midterm(
    *,
    full: bool = True,
    industry: Optional[str] = None,
    performance: Optional[str] = None,
    apply_to_sim: bool = False,
    show_progress: bool = True,
) -> dict:
    from quantpy.midterm_level_alerts import scan_midterm_level_alerts
    from quantpy.midterm_portfolio_advisor import (
        PERFORMANCE_FILTER_OPTIONS,
        build_daily_midterm_operations,
        format_midterm_report_markdown,
        run_midterm_advice,
    )
    from quantpy.portfolio import PortfolioManager

    pm_stats = PortfolioManager().analyze()
    if not pm_stats.get("has_data"):
        return _result(False, "暂无实盘持仓")

    result = run_midterm_advice(
        pm_stats,
        show_progress=show_progress,
        full=full,
        industry=industry,
        performance=performance,
    )
    if not isinstance(result, dict):
        return _result(False, "中线分析失败，请查看运行日志")

    alerts = scan_midterm_level_alerts(pm_stats, result.get("reviews"), save=True)
    result = dict(result)
    result["daily_operations"] = build_daily_midterm_operations(
        pm_stats,
        result.get("reviews", []),
        optimization=result.get("optimization"),
        recommendations=result.get("recommendations"),
        level_alerts=alerts,
    )
    result["markdown"] = format_midterm_report_markdown(result)

    alert_n = alerts.get("alert_count", 0)
    rec_n = len(result.get("recommendations", []))
    select_stats = result.get("select_stats") or {}
    filter_bits = []
    if industry:
        filter_bits.append(f"行业={industry}")
    if performance:
        filter_bits.append(PERFORMANCE_FILTER_OPTIONS.get(performance, performance))
    scan_bit = (
        f"初筛{select_stats.get('prefilter_count', 0)}"
        f"→技术{select_stats.get('scored_pass', 0)}"
    )
    message = (
        f"中线分析完成：复盘 {len(result.get('reviews', []))} 只，推荐 {rec_n} 只（{scan_bit}）"
        + (f"（{' · '.join(filter_bits)}）" if filter_bits else "")
        + (f"，{alert_n} 条价位提醒" if alert_n else "")
    )
    if select_stats.get("fallback_used"):
        message += "；筛选无匹配已回退"

    payload: Dict[str, Any] = {
        "midterm": result,
        "level_alerts": alerts,
        "portfolio_stats": pm_stats,
        "midterm_content": {
            "name": "实盘中线分析报告",
            "content": result.get("markdown") or "",
        },
    }

    if apply_to_sim:
        from quantpy.sim_midterm import apply_midterm_recommendations_to_sim
        from quantpy.sim_replay import SimReplayEngine

        sim_engine = SimReplayEngine()
        sim_engine.reload_state()
        sim_mt = apply_midterm_recommendations_to_sim(
            sim_engine,
            result.get("recommendations", []),
            show_progress=show_progress,
        )
        if isinstance(sim_mt, dict):
            bought_n = len(sim_mt.get("bought", []))
            logged_n = sim_mt.get("pick_logged", 0)
            if logged_n:
                message += f"；模拟记录选股 {logged_n}"
            if bought_n:
                message += f"，买入 {bought_n} 只"
            payload["sim_midterm"] = sim_mt

    return _result(True, message, payload=payload)


def run_action_sim_ma20(
    *,
    force: bool = True,
    show_progress: bool = True,
    industry: Optional[str] = None,
    prefilter: int = 600,
) -> dict:
    """MA20 突破 + MA5 回踩模拟选股（定时 11:00 / 13:30）。"""
    from quantpy.sim_midterm import run_sim_ma20_pullback_select
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    engine.reload_state()
    result = run_sim_ma20_pullback_select(
        engine,
        show_progress=show_progress,
        force=force,
        industry=industry,
        prefilter=prefilter,
    )
    if not isinstance(result, dict):
        return _result(False, "MA20模拟选股失败")
    if result.get("error") or not result.get("ok", True):
        return _result(
            False,
            result.get("message") or "MA20模拟选股失败",
            payload={"sim_midterm_ma20": result},
        )
    return _result(
        True,
        result.get("message") or "MA20模拟选股完成",
        payload={"sim_midterm_ma20": result},
    )


def run_action_review_tune(
    *,
    show_progress: bool = True,
    review_days: int = 90,
    auto_apply: bool = True,
) -> dict:
    """先跑复盘（中线跟进 → AI 学习 → 实盘复盘），再汇总生成选股调优参数。"""
    from quantpy.ai_learning_optimizer import run_ai_learning
    from quantpy.midterm_pick_tracker import run_midterm_tracker_cycle
    from quantpy.real_portfolio_reviewer import run_real_portfolio_review
    from quantpy.selection_tuning import build_selection_tuning, format_tuning_summary

    steps: List[str] = []

    if show_progress:
        print("=" * 60)
        print("复盘 → 选股策略调优")
        print("=" * 60)

    track = run_midterm_tracker_cycle(None, show_progress=show_progress)
    track_summary = (track or {}).get("summary") or {}
    steps.append(
        f"中线跟进：跟踪 {track_summary.get('tracking_count', 0)} 只，"
        f"成熟 {track_summary.get('matured_count', 0)} 只，"
        f"胜率 {track_summary.get('win_rate', 0)}%"
    )

    ai = run_ai_learning(show_progress=show_progress, auto_apply=auto_apply)
    steps.append(
        f"AI学习第 {ai.get('round', 0)} 轮（{ai.get('engine', 'statistical')}）："
        f"模拟样本 {ai.get('sample_count', 0)} 笔"
    )
    if ai.get("param_changes"):
        steps.append(
            "模拟参数：" + ", ".join(f"{k} {v}" for k, v in ai["param_changes"].items())
        )

    real = run_real_portfolio_review(days=max(review_days, 90), show_progress=show_progress)
    if real.get("has_data"):
        rs = real.get("summary") or {}
        steps.append(
            f"实盘复盘：{rs.get('trade_count', 0)} 笔，胜率 {rs.get('win_rate', 0)}%，"
            f"均操作评分 {rs.get('avg_timing_score', 0)}"
        )
    else:
        steps.append("实盘复盘：暂无清盘记录（跳过实盘侧调优）")

    tuning = build_selection_tuning(for_sim=False)
    tuning_sim = build_selection_tuning(for_sim=True)
    summary_text = format_tuning_summary(tuning)

    if show_progress:
        print("\n" + "=" * 60)
        print("选股策略调优（汇总，后续扫描自动生效）")
        print("=" * 60)
        print(summary_text)
        print(
            f"\n模拟盘扫描：超短≥{tuning_sim.ultra_min_score} · "
            f"中线≥{tuning_sim.midterm_min_score} · 三倍量≥{tuning_sim.triple_min_score}"
        )
        if tuning.midterm_condition_bonus or tuning.midterm_tag_bonus:
            print("\n【中线加分因子】")
            for k, v in list(tuning.midterm_condition_bonus.items())[:6]:
                print(f"  条件 {k}: +{v}")
            for k, v in list(tuning.midterm_tag_bonus.items())[:4]:
                print(f"  标签 {k}: +{v}")
        if tuning.midterm_condition_penalty or tuning.midterm_tag_penalty:
            print("\n【中线降权因子】")
            for k, v in list(tuning.midterm_condition_penalty.items())[:6]:
                print(f"  条件 {k}: -{v}")
            for k, v in list(tuning.midterm_tag_penalty.items())[:4]:
                print(f"  标签 {k}: -{v}")

    message = "；".join(steps)
    return _result(
        True,
        message,
        payload={
            "midterm_tracker": track,
            "ai_learning": ai,
            "portfolio_review": real,
            "selection_tuning": tuning.to_dict(),
            "selection_tuning_sim": tuning_sim.to_dict(),
            "tuning_summary": summary_text,
        },
    )


# 供 CLI 映射：command → runner
CLI_ACTION_MAP: Dict[str, Callable[..., dict]] = {
    "scan": run_action_ultra_scan,
    "midterm-triple-volume": run_action_triple_volume,
    "triple-volume-watch": run_action_triple_watch,
    "midterm-track": run_action_midterm_track,
    "sim-ma20": run_action_sim_ma20,
    "review-tune": run_action_review_tune,
}
