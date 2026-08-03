"""
AI 学习优化模块
每 5 个交易日根据超短交易胜率、选股逻辑、买卖点表现进行策略优化。

默认：统计学习引擎（无需 API）。
可选：设置 OPENAI_API_KEY / AI_API_BASE / AI_MODEL 启用 LLM 增强分析。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from quantpy.paths import AI_LEARNING_DIR
from quantpy.sim_replay import SimConfig
from quantpy.trade_journal import TradeJournal

OUTPUT_DIR = AI_LEARNING_DIR

PARAM_BOUNDS: Dict[str, tuple[float, float]] = {
    "min_score": (35, 65),
    "max_open_gap_pct": (4.0, 9.0),
    "min_open_gap_pct": (0.0, 2.0),
    "stop_loss_pct": (-6.0, -2.0),
    "take_profit_pct": (6.0, 15.0),
    "max_hold_days": (2, 5),
    "buy_premium_pct": (0.0, 1.5),
}


class AILearningOptimizer:
    """超短策略学习优化：分析交易样本 → 生成建议 → 可选自动调参。"""

    def __init__(self, auto_apply: bool = True, min_trades: int = 5):
        self.auto_apply = auto_apply
        self.min_trades = min_trades
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def run_learning_cycle(
        self,
        engine: Any,
        review_round: int = 0,
        show_progress: bool = True,
    ) -> dict:
        """执行一轮 AI 学习优化，返回结构化结果。"""
        trades = engine.state.get("closed_trades", [])
        recent = trades[-30:] if trades else []
        sim_df = pd.DataFrame(recent) if recent else pd.DataFrame()
        config = engine.config

        analytics = self._build_analytics(sim_df, config, source="sim")
        real_df = self._load_real_ultra_trades(days=60)
        if not real_df.empty:
            analytics["real"] = self._build_analytics(real_df, config, source="real")

        param_deltas = self._optimize_params(analytics, config)
        selection_changes = self._optimize_selection_params(analytics, config, param_deltas)
        midterm_df = self._load_midterm_closed_trades(engine)
        if not midterm_df.empty:
            analytics["midterm"] = self._build_analytics(midterm_df, config, source="midterm")
            self._apply_midterm_selection(selection_changes, midterm_df)
        try:
            from quantpy.midterm_pick_tracker import (
                load_tracker_summary,
                derive_factor_tuning,
                INTERIM_MIN_SAMPLES,
            )
            tracker = load_tracker_summary(evaluate=False)
            summary = tracker.get("summary") or {}
            interim = summary.get("interim") or tracker.get("interim_summary") or {}
            if summary.get("matured_count", 0) >= 3:
                analytics["midterm_tracker"] = {
                    "matured_count": summary["matured_count"],
                    "win_rate": summary["win_rate"],
                    "avg_return": summary.get("avg_return", 0),
                    "factor_insights": summary.get("factor_insights", [])[:6],
                }
                factor = derive_factor_tuning(summary, interim)
            elif interim.get("interim_count", 0) >= INTERIM_MIN_SAMPLES:
                analytics["midterm_tracker"] = {
                    "interim_count": interim["interim_count"],
                    "win_rate": interim["win_rate"],
                    "avg_return": interim.get("avg_return", 0),
                    "factor_insights": interim.get("factor_insights", [])[:6],
                    "provisional": True,
                }
                factor = derive_factor_tuning(summary, interim)
            else:
                factor = {}
            if factor:
                from quantpy.selection_tuning import (
                    TRIPLE_VOLUME_CONDITION_IDS,
                    TRIPLE_VOLUME_TAGS,
                    MIDTERM_FORBIDDEN_TAG_BONUS,
                )

                if factor.get("midterm_min_score") is not None:
                    selection_changes["midterm_min_score"] = max(
                        selection_changes.get("midterm_min_score", 55),
                        int(factor["midterm_min_score"]),
                    )
                for cond, bonus in (factor.get("midterm_condition_bonus") or {}).items():
                    bucket = (
                        "triple_condition_bonus"
                        if cond in TRIPLE_VOLUME_CONDITION_IDS
                        else "midterm_condition_bonus"
                    )
                    selection_changes.setdefault(bucket, {})[cond] = bonus
                for cond, pen in (factor.get("midterm_condition_penalty") or {}).items():
                    bucket = (
                        "triple_condition_penalty"
                        if cond in TRIPLE_VOLUME_CONDITION_IDS
                        else "midterm_condition_penalty"
                    )
                    selection_changes.setdefault(bucket, {})[cond] = pen
                for tag, bonus in (factor.get("midterm_tag_bonus") or {}).items():
                    if tag in MIDTERM_FORBIDDEN_TAG_BONUS and tag not in TRIPLE_VOLUME_TAGS:
                        continue
                    bucket = (
                        "triple_tag_bonus" if tag in TRIPLE_VOLUME_TAGS else "midterm_tag_bonus"
                    )
                    selection_changes.setdefault(bucket, {})[tag] = bonus
                for tag, pen in (factor.get("midterm_tag_penalty") or {}).items():
                    bucket = (
                        "triple_tag_penalty" if tag in TRIPLE_VOLUME_TAGS else "midterm_tag_penalty"
                    )
                    selection_changes.setdefault(bucket, {})[tag] = pen
                if selection_changes.get("triple_condition_penalty") or selection_changes.get(
                    "triple_tag_penalty"
                ):
                    selection_changes["triple_min_score"] = max(
                        int(selection_changes.get("triple_min_score", 60) or 60), 68,
                    )
        except Exception:
            pass
        suggestions = self._build_suggestions(analytics, param_deltas, config, selection_changes)
        suggestions = self._maybe_llm_enhance(analytics, suggestions, config)

        param_changes: Dict[str, str] = {}
        if self.auto_apply and param_deltas:
            param_changes = self._apply_params(config, param_deltas)

        result = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "round": review_round,
            "engine": "llm" if self._has_llm() else "statistical",
            "sample_count": len(sim_df),
            "analytics": analytics,
            "param_deltas": param_deltas,
            "param_changes": param_changes,
            "selection_changes": selection_changes,
            "suggestions": suggestions,
            "config_after": asdict(config),
            "generated_at": datetime.now().isoformat(),
        }

        self._save_report(result)
        engine.state.setdefault("ai_learning_history", []).append(
            {
                "date": result["date"],
                "round": review_round,
                "engine": result["engine"],
                "sample_count": result["sample_count"],
                "param_changes": param_changes,
                "suggestions": suggestions[:8],
            }
        )

        if show_progress:
            self._print_summary(result)

        return result

    def _load_real_ultra_trades(self, days: int = 60) -> pd.DataFrame:
        journal = TradeJournal()
        df = journal.list_trades(days=days)
        if df.empty:
            return df
        mask = df["strategy"].astype(str).str.contains("超短|涨停|短线", na=False)
        filtered = df[mask].copy()
        if filtered.empty:
            return filtered
        filtered = filtered.rename(
            columns={"profit_pct": "profit_pct", "avg_hold_days": "hold_days"}
        )
        if "hold_days" not in filtered.columns:
            filtered["hold_days"] = 0
        filtered["score"] = 0.0
        filtered["exit_reason"] = filtered.get("note", "").fillna("").replace("", "手动")
        return filtered

    def _build_analytics(self, df: pd.DataFrame, config: SimConfig, source: str) -> dict:
        if df.empty or len(df) < self.min_trades:
            return {
                "source": source,
                "trade_count": len(df),
                "sufficient": False,
            }

        wins = df[df["profit_pct"] > 0]
        stats = {
            "source": source,
            "trade_count": len(df),
            "sufficient": True,
            "win_rate": round(len(wins) / len(df) * 100, 1),
            "avg_profit": round(float(df["profit_pct"].mean()), 2),
            "avg_hold": round(float(df["hold_days"].mean()), 1),
            "total_pnl": round(float(df["profit_amount"].sum()), 2) if "profit_amount" in df else 0,
        }

        score_col = "score" if "score" in df.columns else None
        if score_col:
            stats["by_score"] = self._bucket_stats(
                df,
                score_col,
                [(0, 60, "<60"), (60, 75, "60-75"), (75, 90, "75-90"), (90, 999, "90+")],
            )

        if "exit_reason" in df.columns:
            stats["by_exit"] = self._group_stats(df, "exit_reason")

        if "hold_days" in df.columns:
            stats["by_hold_days"] = self._group_stats(df, "hold_days")

        stats["config_snapshot"] = {
            "min_score": config.min_score,
            "stop_loss_pct": config.stop_loss_pct,
            "take_profit_pct": config.take_profit_pct,
            "max_hold_days": config.max_hold_days,
            "max_open_gap_pct": config.max_open_gap_pct,
            "buy_premium_pct": config.buy_premium_pct,
        }
        return stats

    def _bucket_stats(
        self,
        df: pd.DataFrame,
        col: str,
        buckets: List[tuple[float, float, str]],
    ) -> List[dict]:
        rows = []
        for low, high, label in buckets:
            part = df[(df[col] >= low) & (df[col] < high)]
            if part.empty:
                continue
            wins = part[part["profit_pct"] > 0]
            rows.append({
                "bucket": label,
                "count": len(part),
                "win_rate": round(len(wins) / len(part) * 100, 1),
                "avg_profit": round(float(part["profit_pct"].mean()), 2),
            })
        return rows

    def _group_stats(self, df: pd.DataFrame, col: str) -> List[dict]:
        rows = []
        for key, part in df.groupby(col):
            wins = part[part["profit_pct"] > 0]
            rows.append({
                "key": str(key),
                "count": len(part),
                "win_rate": round(len(wins) / len(part) * 100, 1),
                "avg_profit": round(float(part["profit_pct"].mean()), 2),
            })
        rows.sort(key=lambda x: x["count"], reverse=True)
        return rows

    def _optimize_params(self, analytics: dict, config: SimConfig) -> Dict[str, float]:
        sim = analytics if analytics.get("sufficient") else analytics.get("real", {})
        if not sim.get("sufficient"):
            return {}

        deltas: Dict[str, float] = {}
        win_rate = sim["win_rate"]
        avg_profit = sim["avg_profit"]
        avg_hold = sim.get("avg_hold", 0)

        # --- 选股逻辑 ---
        by_score = sim.get("by_score", [])
        if by_score:
            best = max(by_score, key=lambda x: x["avg_profit"])
            worst = min(by_score, key=lambda x: x["avg_profit"])
            # 高分段表现好 → 抬高入选门槛（注意：55/60 是 min_score，不是评分区间 75-90）
            if best["bucket"] in ("75-90", "90+") and best["win_rate"] >= 55:
                target = 55 if best["bucket"] == "75-90" else 60
                if config.min_score < target:
                    deltas["min_score"] = float(target)
            if worst["bucket"] == "<60" and worst["count"] >= 2 and worst["win_rate"] < 40:
                deltas["min_score"] = float(max(config.min_score + 3, 45))

        if win_rate < 45:
            deltas["min_score"] = float(max(config.min_score + 3, deltas.get("min_score", config.min_score)))
            deltas["max_open_gap_pct"] = float(config.max_open_gap_pct - 0.5)
            deltas["buy_premium_pct"] = float(max(config.buy_premium_pct - 0.1, 0.2))

        if win_rate >= 58 and avg_profit > 2:
            deltas["min_score"] = float(max(config.min_score - 2, 38))

        # --- 买卖点 ---
        by_exit = sim.get("by_exit", [])
        stop_cnt = sum(x["count"] for x in by_exit if "止损" in x["key"])
        tp_cnt = sum(x["count"] for x in by_exit if "止盈" in x["key"])
        expiry_cnt = sum(x["count"] for x in by_exit if "到期" in x["key"])
        total = sim["trade_count"] or 1

        if stop_cnt / total > 0.4:
            deltas["max_open_gap_pct"] = float(config.max_open_gap_pct - 0.5)
            deltas["min_open_gap_pct"] = float(min(config.min_open_gap_pct + 0.2, 1.5))

        if tp_cnt / total < 0.15 and win_rate > 50:
            deltas["take_profit_pct"] = float(config.take_profit_pct - 1.0)

        if expiry_cnt / total > 0.35:
            expiry_profit = next((x["avg_profit"] for x in by_exit if "到期" in x["key"]), 0)
            if expiry_profit < 2:
                deltas["max_hold_days"] = float(config.max_hold_days - 1)

        if avg_profit < -0.5:
            deltas["stop_loss_pct"] = float(config.stop_loss_pct - 0.5)
            deltas["max_hold_days"] = float(max(config.max_hold_days - 1, 2))

        if avg_hold > 2.8:
            deltas["max_hold_days"] = float(config.max_hold_days - 1)

        if avg_profit > 3 and win_rate >= 55:
            deltas["take_profit_pct"] = float(config.take_profit_pct + 0.5)

        return self._clamp_deltas(config, deltas)

    def _load_midterm_closed_trades(self, engine: Any) -> pd.DataFrame:
        mt = engine.state.get("midterm") or {}
        trades = mt.get("closed_trades") or []
        if not trades:
            return pd.DataFrame()
        df = pd.DataFrame(trades)
        if "profit_pct" not in df.columns:
            return pd.DataFrame()
        score_col = "midterm_score" if "midterm_score" in df.columns else "score"
        if score_col in df.columns and "score" not in df.columns:
            df["score"] = df[score_col]
        return df

    def _optimize_selection_params(
        self,
        analytics: dict,
        config: SimConfig,
        param_deltas: Dict[str, float],
    ) -> Dict[str, Any]:
        """根据交易样本推导选股调优参数（超短/中线）。"""
        changes: Dict[str, Any] = {}
        sim = analytics if analytics.get("sufficient") else analytics.get("real", {})
        if not sim.get("sufficient"):
            return changes

        min_score = int(param_deltas.get("min_score", config.min_score))
        changes["ultra_min_score"] = min_score

        win_rate = float(sim.get("win_rate", 0))
        avg_profit = float(sim.get("avg_profit", 0))
        by_exit = sim.get("by_exit", [])
        total = sim.get("trade_count") or 1
        stop_cnt = sum(x["count"] for x in by_exit if "止损" in str(x.get("key", "")))

        if stop_cnt / total > 0.35:
            changes["ultra_penalize_unsealed_above_pct"] = 6.5
            changes["ultra_preferred_tags"] = ["涨停不破开", "强势封板", "封板", "连板"]
            changes.setdefault("ultra_tag_bonus", {})
            changes["ultra_tag_bonus"]["强势封板"] = 6
            changes["ultra_tag_bonus"]["涨停不破开"] = 5

        if win_rate < 45:
            changes["ultra_min_score"] = max(min_score, 43)
            changes["ultra_preferred_tags"] = ["涨停不破开", "强势封板", "封板", "连板"]

        if avg_profit < 0:
            changes["ultra_penalize_3d_gain_above"] = 22.0

        for row in sim.get("by_score", []):
            if row.get("bucket") in ("75-90", "90+") and row.get("win_rate", 0) >= 55:
                changes.setdefault("ultra_tag_bonus", {})
                changes["ultra_tag_bonus"]["涨停不破开"] = max(
                    changes["ultra_tag_bonus"].get("涨停不破开", 0), 5,
                )
            if row.get("bucket") == "<60" and row.get("win_rate", 100) < 40:
                changes["ultra_min_score"] = max(changes.get("ultra_min_score", min_score), 45)
            # 中高分虚高：75-90 胜率差 → 抑制未封板追涨
            if (
                row.get("bucket") == "75-90"
                and int(row.get("count") or 0) >= 5
                and float(row.get("win_rate") or 0) < 40
            ):
                changes["ultra_demote_midhigh_unsealed"] = True
                changes["ultra_penalize_unsealed_above_pct"] = min(
                    float(changes.get("ultra_penalize_unsealed_above_pct") or 99), 5.5,
                )
                changes["ultra_penalize_3d_gain_above"] = min(
                    float(changes.get("ultra_penalize_3d_gain_above") or 99), 18.0,
                )
                changes["ultra_preferred_tags"] = ["涨停不破开", "强势封板", "封板", "连板"]
                changes.setdefault("ultra_tag_bonus", {})
                changes["ultra_tag_bonus"]["强势封板"] = max(
                    changes["ultra_tag_bonus"].get("强势封板", 0), 6,
                )
                changes["ultra_tag_bonus"]["涨停不破开"] = max(
                    changes["ultra_tag_bonus"].get("涨停不破开", 0), 5,
                )

        # 三倍量：观察池大而久无买入信号 → 提高质量门槛
        try:
            from quantpy.triple_volume_watchlist import load_watchlist_summary

            wl = load_watchlist_summary(evaluate=False)
            wsum = wl.get("summary") or {}
            watching = int(wsum.get("watching_count") or 0)
            buys = int(wsum.get("buy_signal_count") or 0)
            if watching >= 40 and buys == 0:
                changes["triple_min_score"] = max(int(changes.get("triple_min_score") or 60), 70)
            elif watching >= 20 and buys <= 1:
                changes["triple_min_score"] = max(int(changes.get("triple_min_score") or 60), 68)
        except Exception:
            pass

        return changes

    def _apply_midterm_selection(
        self,
        changes: Dict[str, Any],
        midterm_df: pd.DataFrame,
    ) -> None:
        if midterm_df.empty or len(midterm_df) < 3:
            return
        win_rate = (midterm_df["profit_pct"] > 0).mean() * 100
        avg_profit = float(midterm_df["profit_pct"].mean())
        if win_rate < 45 or avg_profit < -1:
            changes["midterm_min_score"] = 58
        score_col = "score" if "score" in midterm_df.columns else None
        if score_col:
            for low, high, label in [(0, 55, "<55"), (55, 70, "55-70"), (70, 999, "70+")]:
                part = midterm_df[(midterm_df[score_col] >= low) & (midterm_df[score_col] < high)]
                if part.empty:
                    continue
                wr = (part["profit_pct"] > 0).mean() * 100
                if label == "<55" and len(part) >= 2 and wr < 40:
                    changes["midterm_min_score"] = max(changes.get("midterm_min_score", 55), 60)
                if label in ("55-70", "70+") and wr >= 55 and float(part["profit_pct"].mean()) > 1:
                    changes["midterm_min_score"] = max(55, changes.get("midterm_min_score", 55) - 2)

    def _clamp_deltas(self, config: SimConfig, deltas: Dict[str, float]) -> Dict[str, float]:
        clamped: Dict[str, float] = {}
        for key, val in deltas.items():
            if not hasattr(config, key) or key not in PARAM_BOUNDS:
                continue
            lo, hi = PARAM_BOUNDS[key]
            new_val = round(max(lo, min(hi, val)), 2)
            if new_val != getattr(config, key):
                clamped[key] = new_val
        return clamped

    def _apply_params(self, config: SimConfig, deltas: Dict[str, float]) -> Dict[str, str]:
        changes: Dict[str, str] = {}
        for key, new_val in deltas.items():
            old_val = getattr(config, key)
            setattr(config, key, new_val)
            changes[key] = f"{old_val} → {new_val}"
        return changes

    def _build_suggestions(
        self,
        analytics: dict,
        param_deltas: Dict[str, float],
        config: SimConfig,
        selection_changes: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        suggestions: List[str] = []
        sim = analytics if analytics.get("sufficient") else {}
        real = analytics.get("real", {})

        if not sim.get("sufficient") and not real.get("sufficient"):
            return [
                f"AI 学习：样本不足（模拟 {analytics.get('trade_count', 0)} 笔），"
                f"请继续积累至少 {self.min_trades} 笔超短平仓后再优化。"
            ]

        active = sim if sim.get("sufficient") else real
        src = "模拟盘" if sim.get("sufficient") else "实盘日记"

        suggestions.append(
            f"【{src}】近 {active['trade_count']} 笔超短："
            f"胜率 {active['win_rate']}%，均收益 {active['avg_profit']:+.2f}%，"
            f"均持仓 {active.get('avg_hold', 0)} 天。"
        )

        for row in active.get("by_score", []):
            if row["count"] >= 2:
                suggestions.append(
                    f"选股·入场评分 {row['bucket']}：{row['count']} 笔，"
                    f"胜率 {row['win_rate']}%，均收益 {row['avg_profit']:+.2f}%。"
                )

        best_exit = active.get("by_exit", [])
        if best_exit:
            top = max(best_exit, key=lambda x: x["avg_profit"])
            suggestions.append(
                f"买卖点·最优退出「{top['key']}」：均收益 {top['avg_profit']:+.2f}%"
                f"（{top['count']} 笔）。"
            )
            stop_rows = [x for x in best_exit if "止损" in x["key"]]
            if stop_rows and stop_rows[0]["count"] >= 2:
                s = stop_rows[0]
                suggestions.append(
                    f"买卖点·止损触发 {s['count']} 次，均收益 {s['avg_profit']:+.2f}%："
                    f"优先优化 9:45 选股（理想高开、避免急拉），而非放宽止损。"
                )

        if real.get("sufficient") and sim.get("sufficient"):
            if real["win_rate"] > sim["win_rate"] + 10:
                suggestions.append(
                    "实盘胜率明显高于模拟：模拟选股可更贴近实盘有效策略标签（连板/封板）。"
                )
            elif sim["win_rate"] > real["win_rate"] + 10:
                suggestions.append(
                    "模拟胜率高于实盘：检查实盘执行滑点、追高与 T+1 锁仓对止损的影响。"
                )

        if param_deltas:
            parts = [f"{k}→{v}" for k, v in param_deltas.items()]
            suggestions.append(f"模拟参数优化：{', '.join(parts)}。")
        else:
            suggestions.append("当前模拟参数与样本匹配度尚可，维持现有买卖点纪律。")

        if selection_changes:
            sel_parts = []
            if "ultra_min_score" in selection_changes:
                sel_parts.append(f"超短门槛≥{selection_changes['ultra_min_score']}")
            if "midterm_min_score" in selection_changes:
                sel_parts.append(f"中线门槛≥{selection_changes['midterm_min_score']}")
            if "triple_min_score" in selection_changes:
                sel_parts.append(f"三倍量门槛≥{selection_changes['triple_min_score']}")
            if selection_changes.get("ultra_preferred_tags"):
                sel_parts.append("偏好" + "/".join(selection_changes["ultra_preferred_tags"][:3]))
            if selection_changes.get("ultra_penalize_unsealed_above_pct"):
                sel_parts.append(
                    f"未封板>{selection_changes['ultra_penalize_unsealed_above_pct']}%减分"
                )
            if selection_changes.get("ultra_demote_midhigh_unsealed"):
                sel_parts.append("中高分未封板降权")
            if sel_parts:
                suggestions.append(f"选股参数优化：{' · '.join(sel_parts)}。")
        else:
            suggestions.append("选股参数维持默认，继续积累样本。")

        midterm = analytics.get("midterm") or {}
        if midterm.get("sufficient"):
            suggestions.append(
                f"【模拟中线】近 {midterm['trade_count']} 笔："
                f"胜率 {midterm['win_rate']}%，均收益 {midterm['avg_profit']:+.2f}%。"
            )

        tracker = analytics.get("midterm_tracker") or {}
        if tracker.get("matured_count", 0) >= 3:
            suggestions.append(
                f"【中线跟进】成熟 {tracker['matured_count']} 只，"
                f"胜率 {tracker['win_rate']}%，均收益 {tracker.get('avg_return', 0):+.2f}%。"
            )
            for line in tracker.get("factor_insights", [])[:3]:
                suggestions.append(line)
        elif tracker.get("interim_count", 0) >= 5:
            suggestions.append(
                f"【中线跟进·中间】{tracker['interim_count']} 只，"
                f"胜率 {tracker['win_rate']}%，均收益 {tracker.get('avg_return', 0):+.2f}%"
                f"（provisional）。"
            )
            for line in tracker.get("factor_insights", [])[:3]:
                suggestions.append(line)

        suggestions.append(
            f"超短纪律：以扫描价买入；参考止损 {config.stop_loss_pct}% / "
            f"止盈 +{config.take_profit_pct}% / 最长 {config.max_hold_days} 日；"
            f"min_score≥{config.min_score}。"
        )
        return suggestions

    def _has_llm(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY"))

    def _maybe_llm_enhance(
        self,
        analytics: dict,
        suggestions: List[str],
        config: SimConfig,
    ) -> List[str]:
        if not self._has_llm():
            return suggestions

        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("AI_API_KEY", "")
        base_url = os.environ.get("AI_API_BASE", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("AI_MODEL", "gpt-4o-mini")

        prompt = (
            "你是 A 股超短线量化顾问。根据以下交易统计，给出 3-5 条可执行的策略优化建议"
            "（选股逻辑、买卖点、仓位纪律），每条一句话，不要重复已有内容。\n\n"
            f"统计：{json.dumps(analytics, ensure_ascii=False)}\n"
            f"当前参数：{json.dumps(asdict(config), ensure_ascii=False)}\n"
            f"已有建议：{suggestions}"
        )
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "只输出 JSON 数组，每个元素是一条建议字符串。"},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                },
                timeout=45,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            extra = json.loads(content)
            if isinstance(extra, list):
                merged = suggestions + [str(x) for x in extra if str(x) not in suggestions]
                return merged[:12]
        except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError):
            pass
        return suggestions

    def _save_report(self, result: dict) -> Path:
        fname = OUTPUT_DIR / f"ai_learning_{result['date']}_r{result['round']}.json"
        slim = {k: v for k, v in result.items() if k != "analytics"}
        slim["analytics_summary"] = _summarize_analytics(result.get("analytics", {}))
        fname.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        md_path = OUTPUT_DIR / f"ai_learning_{result['date']}_r{result['round']}.md"
        lines = [
            f"# AI 策略学习报告 第{result['round']}轮\n",
            f"日期: {result['date']} | 引擎: {result['engine']} | 样本: {result['sample_count']} 笔\n\n",
            "## 优化建议\n",
        ]
        for i, s in enumerate(result.get("suggestions", []), 1):
            lines.append(f"{i}. {s}\n")
        if result.get("param_changes"):
            lines.append("\n## 模拟参数调整\n")
            for k, v in result["param_changes"].items():
                lines.append(f"- {k}: {v}\n")
        if result.get("selection_changes"):
            lines.append("\n## 选股参数优化\n")
            for k, v in result["selection_changes"].items():
                lines.append(f"- {k}: {v}\n")
        md_path.write_text("".join(lines), encoding="utf-8")
        return fname

    def _print_summary(self, result: dict) -> None:
        print("\n" + "=" * 60)
        print(f"AI 策略学习（第 {result['round']} 轮 · {result['engine']}）")
        print("=" * 60)
        for i, s in enumerate(result.get("suggestions", [])[:6], 1):
            print(f"  {i}. {s}")
        if result.get("param_changes"):
            print("\n【模拟参数优化】")
            for k, v in result["param_changes"].items():
                print(f"  {k}: {v}")
        if result.get("selection_changes"):
            print("\n【选股参数优化】")
            for k, v in result["selection_changes"].items():
                print(f"  {k}: {v}")


def load_latest_ai_learning() -> dict:
    """读取最近一次 AI 学习结果。"""
    files = sorted(OUTPUT_DIR.glob("ai_learning_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_ai_learning(show_progress: bool = True, auto_apply: bool = True) -> dict:
    from quantpy.sim_replay import SimReplayEngine

    engine = SimReplayEngine()
    optimizer = AILearningOptimizer(auto_apply=auto_apply)
    review_round = int(engine.state.get("review_round", 0)) + 1
    result = optimizer.run_learning_cycle(
        engine, review_round=review_round, show_progress=show_progress
    )
    engine._save_state()
    return result


def _summarize_analytics(analytics: dict) -> dict:
    if not analytics:
        return {}
    out: dict = {}
    for key in ("trade_count", "win_rate", "avg_profit", "sufficient", "source"):
        if key in analytics:
            out[key] = analytics[key]
    for sub in ("by_score", "by_exit", "by_hold_days"):
        if sub in analytics:
            out[sub] = analytics[sub]
    if "real" in analytics:
        out["real"] = _summarize_analytics(analytics["real"])
    return out
