"""
统一选股/模拟调优管线。

单一入口：数据复盘 →（可选）AI 学习 → build_selection_tuning → 同步 SimConfig → 落盘快照。
CLI / Web / sim_replay.run_review 只应调用本模块，不再各自实现「按结果改门槛」。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from quantpy.json_util import sanitize_for_json
from quantpy.paths import DATA_DIR, SELECTION_TUNING_STATE_FILE
from quantpy.selection_tuning import (
    SIM_TUNING_MIN_TRADES,
    SelectionTuning,
    build_selection_tuning,
    format_tuning_summary,
)

logger = logging.getLogger(__name__)


def _atomic_write_json(path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(sanitize_for_json(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def apply_rule_based_sim_params(config: Any, stats: dict) -> Dict[str, str]:
    """规则引擎回退：仅在样本充足时改 SimConfig（与 AI 失败路径共用）。"""
    changes: Dict[str, str] = {}
    n = int(stats.get("trade_count") or 0)
    if n < SIM_TUNING_MIN_TRADES:
        return changes

    win_rate = float(stats.get("win_rate") or 0)
    avg_profit = float(stats.get("avg_profit") or 0)
    avg_hold = float(stats.get("avg_hold") or 0)

    if win_rate < 45:
        old = config.min_score
        config.min_score = min(int(old) + 5, 60)
        if config.min_score != old:
            changes["min_score"] = f"{old} → {config.min_score}"

    if avg_profit < -1:
        old = config.max_open_gap_pct
        config.max_open_gap_pct = max(float(old) - 1.0, 4.0)
        if config.max_open_gap_pct != old:
            changes["max_open_gap_pct"] = f"{old} → {config.max_open_gap_pct}"

    if avg_hold > 2.5:
        old = config.max_hold_days
        config.max_hold_days = max(int(old) - 1, 2)
        if config.max_hold_days != old:
            changes["max_hold_days"] = f"{old} → {config.max_hold_days}"

    if win_rate >= 55 and avg_profit > 2:
        old = config.take_profit_pct
        config.take_profit_pct = min(float(old) + 1.0, 12.0)
        if config.take_profit_pct != old:
            changes["take_profit_pct"] = f"{old} → {config.take_profit_pct}"

    return changes


def sync_sim_config_from_tuning(
    engine: Any,
    tuning_sim: SelectionTuning,
    *,
    auto_apply: bool = True,
) -> Dict[str, str]:
    """把有效超短门槛写回 SimConfig.min_score（单一落盘真相）。"""
    if not auto_apply or engine is None:
        return {}
    changes: Dict[str, str] = {}
    target = int(max(35, min(65, tuning_sim.ultra_min_score)))
    old = int(engine.config.min_score)
    if target != old:
        engine.config.min_score = target
        changes["min_score"] = f"{old} → {target}"
    # 状态里的 config 与内存对齐
    cfg = dict(engine.state.get("config") or {})
    cfg.update(asdict(engine.config))
    engine.state["config"] = cfg
    try:
        engine._save_state()
    except Exception as exc:
        logger.warning("同步 SimConfig 落盘失败: %s", exc)
    return changes


def persist_tuning_snapshot(
    *,
    tuning: SelectionTuning,
    tuning_sim: SelectionTuning,
    steps: Optional[List[str]] = None,
    extras: Optional[dict] = None,
) -> dict:
    """落盘当前有效调优，供看板/下次扫描只读。"""
    payload = {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": list(steps or []),
        "live": tuning.to_dict(),
        "sim": tuning_sim.to_dict(),
        "summary": format_tuning_summary(tuning),
        "extras": extras or {},
    }
    try:
        _atomic_write_json(SELECTION_TUNING_STATE_FILE, payload)
    except OSError as exc:
        logger.warning("写入 selection_tuning_state 失败: %s", exc)
    return payload


def load_tuning_snapshot() -> dict:
    if not SELECTION_TUNING_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SELECTION_TUNING_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_tuning_pipeline(
    *,
    mode: str = "full",
    show_progress: bool = True,
    auto_apply: bool = True,
    review_days: int = 90,
    engine: Any = None,
    review_round: Optional[int] = None,
) -> dict:
    """
    统一调优管线。

    mode:
      - full: 中线跟进 → AI 学习 → 实盘复盘 → 构建调优 → 同步 min_score → 落盘
      - build_only: 仅构建 + 落盘（不重跑复盘）
      - sim_review: 仅 AI 学习（或规则回退）+ 构建 + 同步（供 run_review）
      - ai_only: 仅 AI 学习 + 构建 + 同步
    """
    mode = (mode or "full").strip().lower()
    steps: List[str] = []
    track = None
    ai = None
    real = None
    param_changes: Dict[str, str] = {}
    ai_fallback = False
    errors: List[str] = []

    if show_progress:
        print("=" * 60)
        print(f"调优管线 · mode={mode}")
        print("=" * 60)

    # --- 1) 数据复盘 / 学习 ---
    if mode in ("full",):
        try:
            from quantpy.midterm_pick_tracker import run_midterm_tracker_cycle

            track = run_midterm_tracker_cycle(None, show_progress=show_progress)
            summary = (track or {}).get("summary") or {}
            steps.append(
                f"中线跟进：跟踪 {summary.get('tracking_count', 0)} 只，"
                f"成熟 {summary.get('matured_count', 0)} 只，"
                f"胜率 {summary.get('win_rate', 0)}%"
            )
        except Exception as exc:
            errors.append(f"midterm_tracker:{type(exc).__name__}")
            logger.warning("中线跟进失败: %s", exc)
            steps.append(f"中线跟进失败: {type(exc).__name__}")

    if mode in ("full", "sim_review", "ai_only"):
        try:
            from quantpy.ai_learning_optimizer import AILearningOptimizer, run_ai_learning
            from quantpy.sim_replay import SimReplayEngine

            if engine is None:
                engine = SimReplayEngine()
            if mode == "full" or mode == "ai_only":
                ai = run_ai_learning(show_progress=show_progress, auto_apply=auto_apply)
            else:
                # sim_review：复用传入 engine，避免二次加载状态
                opt = AILearningOptimizer(auto_apply=auto_apply)
                rnd = review_round
                if rnd is None:
                    rnd = int(engine.state.get("review_round", 0)) + 1
                trades = engine.state.get("closed_trades") or []
                if len(trades) >= 10:
                    ai = opt.run_learning_cycle(
                        engine, review_round=int(rnd), show_progress=show_progress,
                    )
                    engine._save_state()
                else:
                    # 样本不足：不跑 AI，留给调用方规则回退
                    ai = {
                        "round": int(rnd),
                        "sample_count": len(trades),
                        "param_changes": {},
                        "suggestions": [
                            f"样本 {len(trades)} 笔(<10)，跳过 AI，可用规则引擎回退"
                        ],
                        "skipped": True,
                    }
            if ai:
                steps.append(
                    f"AI学习第 {ai.get('round', 0)} 轮（{ai.get('engine', 'statistical')}）："
                    f"模拟样本 {ai.get('sample_count', 0)} 笔"
                )
                if ai.get("param_changes"):
                    param_changes.update(ai["param_changes"])
                    steps.append(
                        "模拟参数："
                        + ", ".join(f"{k} {v}" for k, v in ai["param_changes"].items())
                    )
        except Exception as exc:
            errors.append(f"ai_learning:{type(exc).__name__}")
            logger.warning("AI 学习失败: %s", exc)
            ai_fallback = True
            steps.append(f"AI 学习失败，将规则回退: {type(exc).__name__}")
            if engine is not None and auto_apply:
                trades = engine.state.get("closed_trades") or []
                recent = trades[-20:] if trades else []
                if recent:
                    import pandas as pd

                    df = pd.DataFrame(recent)
                    wins = df[df["profit_pct"] > 0]
                    stats = {
                        "trade_count": len(df),
                        "win_rate": round(len(wins) / len(df) * 100, 1) if len(df) else 0,
                        "avg_profit": round(float(df["profit_pct"].mean()), 2) if len(df) else 0,
                        "avg_hold": round(float(df["hold_days"].mean()), 1)
                        if "hold_days" in df.columns and len(df)
                        else 0,
                    }
                    rb = apply_rule_based_sim_params(engine.config, stats)
                    if rb:
                        param_changes.update(rb)
                        try:
                            engine.state["config"] = asdict(engine.config)
                            engine._save_state()
                        except Exception:
                            pass

    if mode in ("full",):
        try:
            from quantpy.real_portfolio_reviewer import run_real_portfolio_review

            real = run_real_portfolio_review(
                days=max(review_days, 90), show_progress=show_progress,
            )
            if real.get("has_data"):
                rs = real.get("summary") or {}
                steps.append(
                    f"实盘复盘：{rs.get('trade_count', 0)} 笔，胜率 {rs.get('win_rate', 0)}%，"
                    f"均操作评分 {rs.get('avg_timing_score', 0)}"
                )
            else:
                steps.append("实盘复盘：暂无清盘记录（跳过实盘侧调优）")
        except Exception as exc:
            errors.append(f"real_review:{type(exc).__name__}")
            logger.warning("实盘复盘失败: %s", exc)
            steps.append(f"实盘复盘失败: {type(exc).__name__}")

    # --- 2) 单一构建入口 ---
    tuning = build_selection_tuning(for_sim=False)
    tuning_sim = build_selection_tuning(for_sim=True)
    summary_text = format_tuning_summary(tuning)
    steps.append(
        f"有效门槛：超短≥{tuning.ultra_min_score} · 中线≥{tuning.midterm_min_score} · "
        f"MA20≥{tuning.ma20_pullback_min_score} · 三倍量≥{tuning.triple_min_score}"
    )

    # --- 3) 同步 SimConfig ---
    if engine is None and mode in ("full", "build_only", "ai_only"):
        try:
            from quantpy.sim_replay import SimReplayEngine

            engine = SimReplayEngine()
        except Exception as exc:
            logger.warning("加载模拟引擎失败: %s", exc)
    sync_changes = sync_sim_config_from_tuning(
        engine, tuning_sim, auto_apply=auto_apply,
    )
    if sync_changes:
        param_changes.update({f"sync_{k}": v for k, v in sync_changes.items()})
        steps.append("已同步 SimConfig.min_score ← tuning_sim.ultra_min_score")

    # --- 4) 落盘 ---
    snapshot = persist_tuning_snapshot(
        tuning=tuning,
        tuning_sim=tuning_sim,
        steps=steps,
        extras={
            "mode": mode,
            "param_changes": param_changes,
            "ai_fallback": ai_fallback,
            "errors": errors,
        },
    )

    if show_progress:
        print("\n" + "=" * 60)
        print("选股策略调优（统一管线落盘）")
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
        path = SELECTION_TUNING_STATE_FILE
        print(f"\n已写入: {path}")

    return {
        "ok": len(errors) == 0 or tuning is not None,
        "mode": mode,
        "message": "；".join(steps),
        "steps": steps,
        "errors": errors,
        "ai_fallback": ai_fallback,
        "param_changes": param_changes,
        "midterm_tracker": track,
        "ai_learning": ai,
        "portfolio_review": real,
        "selection_tuning": tuning.to_dict(),
        "selection_tuning_sim": tuning_sim.to_dict(),
        "tuning_summary": summary_text,
        "snapshot": snapshot,
    }
