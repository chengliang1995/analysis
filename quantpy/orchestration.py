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


def run_action_short_term(
    *,
    max_candidates: int = 50,
    max_analyze: int = 400,
    show_progress: bool = True,
) -> dict:
    """短线强势股（涨停基因+均线多头），独立于 ultra scan。"""
    from quantpy.short_term_picker import run_short_term_pick

    result = run_short_term_pick(
        max_candidates=max_candidates,
        max_analyze=max_analyze,
        show_progress=show_progress,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return _result(
            False,
            (result or {}).get("message") if isinstance(result, dict) else "短线筛选失败",
            payload={"short_term": result or {}},
        )
    stats = result.get("stats") or {}
    message = (
        f"短线强势筛选完成：通过 {stats.get('passed', 0)} · "
        f"返回 {stats.get('returned', 0)}（来源 {result.get('source')}）"
    )
    artifacts = [result["artifact"]] if result.get("artifact") else []
    return _result(True, message, payload={"short_term": result}, artifacts=artifacts)


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


def run_action_sim_serenity(
    *,
    theme: str = "",
    board_type: str = "concept",
    board_code: Optional[str] = None,
    force: bool = False,
    show_progress: bool = True,
    max_candidates: int = 12,
) -> dict:
    """Serenity 卡脖子模拟选股（独立 20 万账户）。"""
    from quantpy.sim_replay import SimReplayEngine
    from quantpy.sim_serenity import run_sim_serenity_select

    engine = SimReplayEngine()
    engine.reload_state()
    result = run_sim_serenity_select(
        engine,
        theme=theme,
        board_type=board_type,
        board_code=board_code,
        show_progress=show_progress,
        force=force,
        max_candidates=max_candidates,
    )
    if not isinstance(result, dict):
        return _result(False, "Serenity 模拟选股失败")
    if result.get("error") or not result.get("ok", True):
        return _result(
            False,
            result.get("message") or "Serenity 模拟选股失败",
            payload={"sim_serenity": result},
        )
    return _result(
        True,
        result.get("message") or "Serenity 模拟选股完成",
        payload={"sim_serenity": result},
    )


def run_action_sim_short_term(
    *,
    force: bool = False,
    show_progress: bool = True,
    max_candidates: int = 30,
    max_analyze: int = 400,
) -> dict:
    """短线强势模拟选股（独立 20 万账户）。"""
    from quantpy.sim_replay import SimReplayEngine
    from quantpy.sim_short_term import run_sim_short_term_select

    engine = SimReplayEngine()
    engine.reload_state()
    result = run_sim_short_term_select(
        engine,
        show_progress=show_progress,
        force=force,
        max_candidates=max_candidates,
        max_analyze=max_analyze,
    )
    if not isinstance(result, dict):
        return _result(False, "短线模拟选股失败")
    if result.get("error") or not result.get("ok", True):
        return _result(
            False,
            result.get("message") or "短线模拟选股失败",
            payload={"sim_short_term": result},
        )
    return _result(
        True,
        result.get("message") or "短线模拟选股完成",
        payload={"sim_short_term": result},
    )


def run_action_review_tune(
    *,
    show_progress: bool = True,
    review_days: int = 90,
    auto_apply: bool = True,
) -> dict:
    """统一调优管线：中线跟进 → AI 学习 → 实盘复盘 → 构建/同步/落盘。"""
    from quantpy.tuning_pipeline import run_tuning_pipeline

    out = run_tuning_pipeline(
        mode="full",
        show_progress=show_progress,
        auto_apply=auto_apply,
        review_days=review_days,
    )
    return _result(
        bool(out.get("ok", True)),
        out.get("message") or "复盘调优完成",
        payload={
            "midterm_tracker": out.get("midterm_tracker"),
            "ai_learning": out.get("ai_learning"),
            "portfolio_review": out.get("portfolio_review"),
            "selection_tuning": out.get("selection_tuning"),
            "selection_tuning_sim": out.get("selection_tuning_sim"),
            "tuning_summary": out.get("tuning_summary"),
            "param_changes": out.get("param_changes"),
            "pipeline_mode": out.get("mode"),
            "errors": out.get("errors") or [],
        },
    )


def run_action_ai_learn(
    *,
    show_progress: bool = True,
    auto_apply: bool = True,
) -> dict:
    """仅 AI 学习 + 构建调优（统一管线 ai_only）。"""
    from quantpy.tuning_pipeline import run_tuning_pipeline

    out = run_tuning_pipeline(
        mode="ai_only",
        show_progress=show_progress,
        auto_apply=auto_apply,
    )
    return _result(
        bool(out.get("ok", True)),
        out.get("message") or "AI 学习完成",
        payload={
            "ai_learning": out.get("ai_learning"),
            "selection_tuning": out.get("selection_tuning"),
            "selection_tuning_sim": out.get("selection_tuning_sim"),
            "tuning_summary": out.get("tuning_summary"),
            "param_changes": out.get("param_changes"),
        },
    )


def run_action_sim(*, force: bool = False, show_progress: bool = False) -> dict:
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    result = engine.run_daily(force_select=force, show_progress=show_progress)
    closed = result.get("closed_today", 0) if isinstance(result, dict) else 0
    picks = result.get("picks_today", 0) if isinstance(result, dict) else 0
    return _result(
        True,
        f"模拟运行完成：平仓 {closed} 笔，新选 {picks} 只",
        payload={"sim_daily": result or {}},
    )


def run_action_sim_review(*, show_progress: bool = False) -> dict:
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    review = engine.run_review(show_progress=show_progress)
    round_no = review.get("round", 0) if isinstance(review, dict) else 0
    return _result(
        True,
        f"第 {round_no} 轮复盘完成",
        payload={
            "review": {
                "round": (review or {}).get("round"),
                "suggestions": (review or {}).get("suggestions", []),
                "ai_learning": (review or {}).get("ai_learning"),
                "tuning_summary": (review or {}).get("tuning_summary"),
                "stats": (review or {}).get("stats"),
            }
        },
    )


def run_action_sim_backtest(*, days: int = 20, show_progress: bool = False) -> dict:
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    result = engine.replay_backtest(days=days, show_progress=show_progress)
    if isinstance(result, dict) and result:
        message = (
            f"回测完成：权益 {result.get('equity', 0):,.0f} 元 "
            f"({result.get('total_return_pct', 0):+.2f}%)，"
            f"平仓 {result.get('closed_count', 0)} 笔"
        )
        return _result(True, message, payload={"backtest": result})
    return _result(True, "回测完成", payload={"backtest": result or {}})


def run_action_sim_midterm_select(
    *,
    force: bool = False,
    show_progress: bool = True,
    industry: Optional[str] = None,
    performance: Optional[str] = None,
    use_cache: bool = False,
) -> dict:
    from quantpy.sim_midterm import run_sim_midterm_select
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    engine.reload_state()
    result = run_sim_midterm_select(
        engine,
        show_progress=show_progress,
        force=force,
        industry=industry,
        performance=performance,
        use_cache=use_cache,
    )
    if result is None:
        return _result(False, "模拟中线选股失败")
    if isinstance(result, dict) and result.get("ok"):
        return _result(
            True,
            result.get("message") or "模拟中线选股完成",
            payload={"sim_midterm": result},
        )
    if isinstance(result, dict):
        return _result(
            False,
            result.get("message") or "无推荐标的",
            payload={"sim_midterm": result},
        )
    return _result(False, "模拟中线选股失败")


def run_action_sim_midterm(*, show_progress: bool = True) -> dict:
    from quantpy.sim_midterm import (
        check_midterm_exits,
        enrich_midterm_sim,
        run_midterm_sim_review,
    )
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    engine.reload_state()
    check_midterm_exits(engine, show_progress=show_progress)
    reviews = run_midterm_sim_review(engine, show_progress=show_progress)
    summary = enrich_midterm_sim(engine.state)
    n = len(reviews or [])
    return _result(
        True,
        f"模拟中线复盘完成：{n} 只持仓",
        payload={"sim_midterm": {"reviews": reviews, "summary": summary}},
    )


def run_action_real_review(*, days: int = 90, show_progress: bool = False) -> dict:
    from quantpy.real_portfolio_reviewer import run_real_portfolio_review

    result = run_real_portfolio_review(days=days, show_progress=show_progress)
    count = (result or {}).get("summary", {}).get("trade_count", 0) if isinstance(result, dict) else 0
    payload: dict = {"portfolio_review": result or {}}
    if isinstance(result, dict) and result.get("markdown"):
        payload["review_content"] = {
            "name": "实盘操作复盘",
            "content": result["markdown"],
        }
    return _result(True, f"实盘复盘完成：分析 {count} 笔平仓", payload=payload)


def run_action_sector(
    *,
    board_type: str = "concept",
    board_code: Optional[str] = None,
    top_boards: int = 8,
    stocks_per_board: int = 5,
    show_progress: bool = True,
) -> dict:
    from quantpy.sector_recommender import run_sector_recommendations

    result = run_sector_recommendations(
        board_type=board_type,
        board_code=board_code,
        top_boards=top_boards,
        stocks_per_board=stocks_per_board,
        show_progress=show_progress,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return _result(
            False,
            (result or {}).get("message") if isinstance(result, dict) else "板块推荐失败",
            payload={"sector": result or {}},
        )
    stats = result.get("stats") or {}
    label = result.get("board_type_label") or "板块"
    message = (
        f"{label}推荐完成：{stats.get('board_count', 0)} 个板块 · "
        f"{stats.get('stock_count', 0)} 只标的"
    )
    return _result(True, message, payload={"sector": result})


def run_action_serenity(
    *,
    theme: str = "",
    board_type: str = "concept",
    board_code: Optional[str] = None,
    top_boards: int = 3,
    max_candidates: int = 12,
    record_picks: bool = True,
    show_progress: bool = True,
) -> dict:
    """Serenity 卡脖子主题选股（独立桶，不写 midterm_pick_tracker）。"""
    from quantpy.serenity_choke_advisor import run_serenity_scan

    result = run_serenity_scan(
        theme,
        board_type=board_type,
        board_code=board_code,
        top_boards=top_boards,
        max_candidates=max_candidates,
        record_picks=record_picks,
        show_progress=show_progress,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        return _result(
            False,
            (result or {}).get("message") if isinstance(result, dict) else "Serenity 扫描失败",
            payload={"serenity": result or {}},
        )
    stats = result.get("stats") or {}
    message = (
        f"Serenity 完成：主题「{result.get('theme') or board_code}」· "
        f"板块 {stats.get('board_count', 0)} · 候选 {stats.get('candidate_count', 0)}"
    )
    return _result(True, message, payload={"serenity": result})


def run_action_serenity_track(*, show_progress: bool = True) -> dict:
    from quantpy.serenity_choke_advisor import run_serenity_track

    result = run_serenity_track(show_progress=show_progress)
    summary = (result or {}).get("summary") or {}
    message = (
        f"Serenity 跟进完成：开放 {summary.get('open_n')} · "
        f"闭环 {summary.get('closed_n')} · 胜率 {summary.get('win_rate')}"
    )
    return _result(True, message, payload={"serenity_track": result or {}})


def run_action_alerts(*, show_progress: bool = False) -> dict:
    from quantpy.midterm_level_alerts import scan_midterm_level_alerts
    from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor
    from quantpy.portfolio import PortfolioManager

    pm_stats = PortfolioManager().analyze()
    if not pm_stats.get("has_data"):
        return _result(False, "暂无实盘持仓")
    midterm = MidtermPortfolioAdvisor().run_quick_advice(pm_stats)
    result = scan_midterm_level_alerts(
        pm_stats,
        midterm.get("reviews") if isinstance(midterm, dict) else None,
        save=True,
    )
    n = result.get("alert_count", 0) if isinstance(result, dict) else 0
    message = f"价位提醒检查完成：{n} 条" if n else "价位提醒检查完成：暂无触发"
    return _result(True, message, payload={"level_alerts": result or {}})


def run_action_refresh() -> dict:
    from quantpy.web_dashboard import refresh_holdings_quotes

    portfolio_stats, sim_data, log = refresh_holdings_quotes()
    n_real = len(portfolio_stats.get("positions", []))
    n_sim = sim_data.get("position_count", 0)
    return _result(
        True,
        f"持仓行情已刷新（实盘 {n_real} 只 · 模拟 {n_sim} 只）",
        payload={
            "portfolio_stats": portfolio_stats,
            "sim_data": sim_data,
            "refresh_log": log,
        },
    )


# 供 CLI / Web 映射：command → runner
CLI_ACTION_MAP: Dict[str, Callable[..., dict]] = {
    "scan": run_action_ultra_scan,
    "short-term": run_action_short_term,
    "midterm-triple-volume": run_action_triple_volume,
    "triple-volume-watch": run_action_triple_watch,
    "midterm-track": run_action_midterm_track,
    "sim-ma20": run_action_sim_ma20,
    "sim-serenity": run_action_sim_serenity,
    "sim-short-term": run_action_sim_short_term,
    "review-tune": run_action_review_tune,
    "ai-learn": run_action_ai_learn,
    "sim": run_action_sim,
    "sim-review": run_action_sim_review,
    "sim-backtest": run_action_sim_backtest,
    "sim-midterm": run_action_sim_midterm,
    "sim-midterm-select": run_action_sim_midterm_select,
    "review": run_action_real_review,
    "sector": run_action_sector,
    "serenity": run_action_serenity,
    "serenity-track": run_action_serenity_track,
    "alerts": run_action_alerts,
    "refresh": run_action_refresh,
}

# Web 别名（按钮 data-action 与 CLI 略有差异时在此对齐）
WEB_ACTION_ALIASES: Dict[str, str] = {
    "sim-ma20-select": "sim-ma20",
    "sim-serenity-select": "sim-serenity",
    "sim-short-term-select": "sim-short-term",
}


def dispatch_action(action: str, **kwargs: Any) -> dict:
    """统一分发。未知 action 返回 ok=False。"""
    key = WEB_ACTION_ALIASES.get(action, action)
    runner = CLI_ACTION_MAP.get(key)
    if runner is None:
        return _result(False, f"未知操作: {action}")
    # midterm / report / sim-ma20 等已在 map 外单独注册的也要覆盖
    return runner(**kwargs)


# midterm / report 此前已定义，补入 map
CLI_ACTION_MAP["midterm"] = run_action_midterm
CLI_ACTION_MAP["report"] = run_action_report

