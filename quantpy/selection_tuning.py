"""根据模拟/实盘复盘记录，动态调整超短与中线选股参数。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from quantpy.paths import SIM_REVIEW_DIR, SIM_STATE_FILE

ULTRA_SHORT_STRATEGIES = frozenset({"超短", "涨停", "短线"})

# 强势标签别名：复盘偏好「封板」时，连板/高换手等同类信号一并认可
ULTRA_STRONG_TAG_GROUPS: Dict[str, List[str]] = {
    "封板": ["封板", "强势封板", "涨停不破开", "连板"],
    "强势封板": ["强势封板", "封板", "涨停不破开", "连板"],
    "涨停不破开": ["涨停不破开", "强势封板", "封板", "连板"],
    "连板": ["连板", "封板", "强势封板"],
    "高换手": ["高换手", "放量"],
}


@dataclass
class SelectionTuning:
    """选股调优参数（由复盘记录推导）。"""

    ultra_min_score: int = 35
    midterm_min_score: int = 65
    ultra_tag_bonus: Dict[str, int] = field(default_factory=dict)
    ultra_tag_penalty: Dict[str, int] = field(default_factory=dict)
    ultra_penalize_3d_gain_above: Optional[float] = None
    ultra_penalize_pct_above: Optional[float] = None
    ultra_penalize_unsealed_above_pct: Optional[float] = None
    ultra_preferred_tags: Optional[List[str]] = None
    require_ultra_tag_any: Optional[List[str]] = None
    strict_tag_filter: bool = False
    midterm_ma20_chase_penalty: int = 0
    midterm_ma20_chase_ratio: float = 1.08
    midterm_penalize_ret_20d_below: Optional[float] = None
    midterm_condition_bonus: Dict[str, int] = field(default_factory=dict)
    midterm_condition_penalty: Dict[str, int] = field(default_factory=dict)
    midterm_tag_bonus: Dict[str, int] = field(default_factory=dict)
    midterm_tag_penalty: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _bucket_score_stats(df: pd.DataFrame, col: str = "score") -> List[dict]:
    if df.empty or col not in df.columns:
        return []
    buckets = [(0, 60, "<60"), (60, 75, "60-75"), (75, 90, "75-90"), (90, 999, "90+")]
    rows: List[dict] = []
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


def _load_sim_closed_trades() -> pd.DataFrame:
    if not SIM_STATE_FILE.exists():
        return pd.DataFrame()
    try:
        state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return pd.DataFrame()
    trades = state.get("closed_trades") or []
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    if "profit_pct" not in df.columns:
        return pd.DataFrame()
    return df


def _load_latest_sim_review_stats() -> dict:
    files = sorted(SIM_REVIEW_DIR.glob("review_*.md"), reverse=True)
    if not files:
        return {}
    state_stats: dict = {}
    try:
        state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
        history = state.get("param_history") or []
        if history:
            last = history[-1]
            state_stats = last.get("stats") or {}
    except (OSError, json.JSONDecodeError):
        pass
    return state_stats


def _apply_sim_trade_insights(tuning: SelectionTuning, df: pd.DataFrame) -> None:
    if df.empty or len(df) < 3:
        return

    win_rate = (df["profit_pct"] > 0).mean() * 100
    avg_profit = float(df["profit_pct"].mean())
    tuning.sources.append(f"sim_trades(n={len(df)})")

    if win_rate < 45:
        tuning.ultra_min_score = max(tuning.ultra_min_score, 40)
        tuning.ultra_penalize_unsealed_above_pct = 7.0
        tuning.ultra_tag_bonus.setdefault("涨停不破开", 8)
        tuning.ultra_tag_bonus.setdefault("强势封板", 6)
        tuning.ultra_preferred_tags = ["涨停不破开", "强势封板", "封板", "连板"]
        tuning.notes.append(f"模拟复盘胜率 {win_rate:.0f}% 偏低，偏好封板/不破开（软筛选）")

    if avg_profit < 0:
        tuning.ultra_min_score = max(tuning.ultra_min_score, 42)
        tuning.ultra_penalize_3d_gain_above = 22.0
        tuning.notes.append(f"模拟均收益 {avg_profit:+.2f}% 为负，抑制 3 日大涨追高")

    by_score = _bucket_score_stats(df, "score")
    for row in by_score:
        if row["bucket"] == "<60" and row["count"] >= 2 and row["win_rate"] < 40:
            tuning.ultra_min_score = max(tuning.ultra_min_score, 43)
            tuning.notes.append("低评分(<60)标的胜率差，适度抬高入选门槛")
        if row["bucket"] in ("75-90", "90+") and row["win_rate"] >= 55 and row["avg_profit"] > 1:
            tuning.ultra_tag_bonus.setdefault("涨停不破开", 5)
            tuning.notes.append(f"评分 {row['bucket']} 区间表现较好，强化同类信号权重")

    stop_heavy = df[df["exit_reason"].astype(str).str.contains("止损", na=False)]
    if len(stop_heavy) >= max(2, len(df) * 0.35):
        tuning.ultra_penalize_unsealed_above_pct = min(
            tuning.ultra_penalize_unsealed_above_pct or 99, 6.5,
        )
        tuning.notes.append("止损触发偏多，未封板大涨标的减分")


def _apply_ai_learning(tuning: SelectionTuning, ai: dict) -> None:
    if not ai:
        return
    tuning.sources.append("ai_learning")

    cfg = ai.get("config_after") or {}
    if cfg.get("min_score") is not None:
        tuning.ultra_min_score = max(tuning.ultra_min_score, int(cfg["min_score"]))

    sel = ai.get("selection_changes") or {}
    if sel.get("ultra_min_score") is not None:
        tuning.ultra_min_score = max(tuning.ultra_min_score, int(sel["ultra_min_score"]))
    if sel.get("midterm_min_score") is not None:
        tuning.midterm_min_score = max(tuning.midterm_min_score, int(sel["midterm_min_score"]))
    for cond, bonus in (sel.get("midterm_condition_bonus") or {}).items():
        tuning.midterm_condition_bonus[cond] = max(
            tuning.midterm_condition_bonus.get(cond, 0), int(bonus),
        )
    for cond, penalty in (sel.get("midterm_condition_penalty") or {}).items():
        tuning.midterm_condition_penalty[cond] = max(
            tuning.midterm_condition_penalty.get(cond, 0), int(penalty),
        )
    for tag, bonus in (sel.get("midterm_tag_bonus") or {}).items():
        tuning.midterm_tag_bonus[tag] = max(tuning.midterm_tag_bonus.get(tag, 0), int(bonus))
    for tag, penalty in (sel.get("midterm_tag_penalty") or {}).items():
        tuning.midterm_tag_penalty[tag] = max(
            tuning.midterm_tag_penalty.get(tag, 0), int(penalty),
        )
    if sel.get("ultra_penalize_3d_gain_above") is not None:
        tuning.ultra_penalize_3d_gain_above = sel["ultra_penalize_3d_gain_above"]
    if sel.get("ultra_penalize_unsealed_above_pct") is not None:
        tuning.ultra_penalize_unsealed_above_pct = min(
            tuning.ultra_penalize_unsealed_above_pct or 99,
            float(sel["ultra_penalize_unsealed_above_pct"]),
        )
    if sel.get("ultra_preferred_tags"):
        tuning.ultra_preferred_tags = list(sel["ultra_preferred_tags"])
    for tag, bonus in (sel.get("ultra_tag_bonus") or {}).items():
        tuning.ultra_tag_bonus[tag] = max(tuning.ultra_tag_bonus.get(tag, 0), int(bonus))

    analytics = ai.get("analytics") or {}
    primary = analytics if analytics.get("sufficient") else analytics.get("real") or {}
    if not primary.get("sufficient"):
        return

    win_rate = float(primary.get("win_rate", 0))
    if win_rate < 45:
        tuning.ultra_min_score = max(tuning.ultra_min_score, 43)
        tuning.ultra_tag_bonus.setdefault("强势封板", 5)
    elif win_rate >= 58:
        tuning.ultra_min_score = max(35, tuning.ultra_min_score - 2)

    for row in primary.get("by_score", []):
        if row.get("bucket") == "<60" and row.get("win_rate", 100) < 40:
            tuning.ultra_min_score = max(tuning.ultra_min_score, 45)

    midterm = analytics.get("midterm") or {}
    if midterm.get("sufficient") and float(midterm.get("win_rate", 100)) < 45:
        tuning.midterm_min_score = max(tuning.midterm_min_score, 58)
    elif midterm.get("sufficient") and float(midterm.get("win_rate", 100)) >= 55:
        # 模拟中线表现尚可，允许 AI 建议适度放宽（但不低于默认 65）
        if sel.get("midterm_min_score") is not None:
            tuning.midterm_min_score = max(tuning.midterm_min_score, 65)


def _apply_midterm_tracker(tuning: SelectionTuning) -> None:
    try:
        from quantpy.midterm_pick_tracker import (
            load_tracker_summary,
            derive_factor_tuning,
            INTERIM_MIN_SAMPLES,
        )
        # 选股时只读跟进结果，评估由「中线跟进评估」或学习周期触发
        payload = load_tracker_summary(evaluate=False)
        summary = payload.get("summary") or {}
        interim = summary.get("interim") or payload.get("interim_summary") or {}
        matured = summary.get("matured_count", 0)
        if matured < 3 and interim.get("interim_count", 0) < INTERIM_MIN_SAMPLES:
            return
        tuning.sources.append(
            "midterm_tracker" if matured >= 3 else "midterm_tracker_interim",
        )
        factor = derive_factor_tuning(summary, interim)
        for cond, bonus in (factor.get("midterm_condition_bonus") or {}).items():
            if int(bonus) >= 0:
                tuning.midterm_condition_bonus[cond] = max(
                    tuning.midterm_condition_bonus.get(cond, 0), int(bonus),
                )
            else:
                tuning.midterm_condition_penalty[cond] = max(
                    tuning.midterm_condition_penalty.get(cond, 0), abs(int(bonus)),
                )
        for cond, penalty in (factor.get("midterm_condition_penalty") or {}).items():
            tuning.midterm_condition_penalty[cond] = max(
                tuning.midterm_condition_penalty.get(cond, 0), int(penalty),
            )
        for tag, bonus in (factor.get("midterm_tag_bonus") or {}).items():
            if int(bonus) >= 0:
                tuning.midterm_tag_bonus[tag] = max(
                    tuning.midterm_tag_bonus.get(tag, 0), int(bonus),
                )
            else:
                tuning.midterm_tag_penalty[tag] = max(
                    tuning.midterm_tag_penalty.get(tag, 0), abs(int(bonus)),
                )
        for tag, penalty in (factor.get("midterm_tag_penalty") or {}).items():
            tuning.midterm_tag_penalty[tag] = max(
                tuning.midterm_tag_penalty.get(tag, 0), int(penalty),
            )
        if factor.get("midterm_min_score") is not None:
            tuning.midterm_min_score = max(
                tuning.midterm_min_score, int(factor["midterm_min_score"]),
            )
        tuning.notes.extend((factor.get("notes") or [])[:4])
        if matured >= 3:
            wr = summary.get("win_rate", 0)
            tuning.notes.append(
                f"中线跟进{summary.get('follow_trading_days', 10)}日胜率 {wr}%"
                f"（{summary['matured_count']} 笔），因子已调优"
            )
        elif interim.get("interim_count", 0) >= INTERIM_MIN_SAMPLES:
            tuning.notes.append(
                f"中线中间统计胜率 {interim.get('win_rate', 0)}%"
                f"（{interim['interim_count']} 只），因子 provisional 调优"
            )
    except Exception:
        pass


def _apply_real_review(tuning: SelectionTuning, review: dict) -> None:
    if not review.get("has_data"):
        return
    tuning.sources.append("real_review")

    summary = review.get("summary") or {}
    trade_reviews = review.get("trade_reviews") or []
    n = len(trade_reviews) or int(summary.get("trade_count") or 0)

    high_buy = sum(1 for t in trade_reviews if t.get("buy_timing") == "偏高")
    if n and high_buy >= max(2, n * 0.35):
        tuning.midterm_ma20_chase_penalty = max(tuning.midterm_ma20_chase_penalty, 12)
        tuning.midterm_ma20_chase_ratio = 1.06
        tuning.ultra_penalize_3d_gain_above = 24.0
        tuning.notes.append(f"实盘 {high_buy} 笔买入偏高，抑制追高（MA20 上方/3日大涨）")

    early_sell = sum(1 for t in trade_reviews if t.get("sell_timing") == "偏早")
    if early_sell >= 2:
        tuning.ultra_tag_bonus.setdefault("涨停不破开", 4)
        tuning.notes.append("存在盈利卖偏早，选股侧更重视可持续强势（不破开）")

    for strat in review.get("by_strategy") or []:
        name = str(strat.get("strategy") or "")
        if name in ULTRA_SHORT_STRATEGIES and strat.get("win_rate", 100) < 50:
            tuning.ultra_preferred_tags = ["涨停不破开", "强势封板", "封板", "连板", "高换手"]
            tuning.ultra_tag_bonus.setdefault("强势封板", 6)
            tuning.ultra_tag_bonus.setdefault("涨停不破开", 5)
            tuning.ultra_min_score = max(tuning.ultra_min_score, 38)
            trade_n = int(strat.get("count") or strat.get("trade_count") or 0)
            win_rate = float(strat.get("win_rate", 100))
            tuning.notes.append(
                f"实盘「{name}」胜率 {win_rate}% 偏低，偏好封板类强势信号（软筛选）"
            )
            if win_rate < 25 and trade_n >= 8:
                tuning.require_ultra_tag_any = ["涨停不破开", "强势封板", "封板", "连板"]
                tuning.strict_tag_filter = True
                tuning.notes.append("样本充足且胜率极低，启用封板类硬筛")

    for strat in review.get("by_strategy") or []:
        name = str(strat.get("strategy") or "")
        if name not in ULTRA_SHORT_STRATEGIES and strat.get("avg_profit", 0) < -3:
            tuning.midterm_min_score = max(tuning.midterm_min_score, 58)
            tuning.midterm_penalize_ret_20d_below = -8.0
            tuning.notes.append(f"中线策略「{name}」均收益偏弱，提高技术评分门槛")

    avg_timing = float(summary.get("avg_timing_score") or 100)
    if avg_timing < 58:
        tuning.midterm_min_score = max(tuning.midterm_min_score, 57)
        tuning.ultra_min_score = max(tuning.ultra_min_score, 40)
        tuning.notes.append(f"实盘操作评分均值 {avg_timing:.0f} 偏低，整体收紧选股")

    good = [t for t in trade_reviews if float(t.get("profit_pct", 0)) >= 8]
    if good:
        tuning.ultra_tag_bonus.setdefault("涨停不破开", 3)


def build_selection_tuning(*, for_sim: bool = False) -> SelectionTuning:
    """汇总模拟复盘、AI 学习、实盘复盘，生成选股调优参数。"""
    tuning = SelectionTuning()

    try:
        from quantpy.sim_replay import SimReplayEngine

        engine = SimReplayEngine()
        base_min = int(engine.config.min_score)
        tuning.ultra_min_score = max(tuning.ultra_min_score, base_min)
        tuning.sources.append("sim_config")
        if for_sim:
            tuning.ultra_min_score = min(tuning.ultra_min_score, max(35, base_min - 3))
    except Exception:
        pass

    sim_df = _load_sim_closed_trades()
    _apply_sim_trade_insights(tuning, sim_df)

    review_stats = _load_latest_sim_review_stats()
    if review_stats.get("win_rate", 100) < 45:
        tuning.ultra_min_score = max(tuning.ultra_min_score, 40)

    from quantpy.ai_learning_optimizer import load_latest_ai_learning
    from quantpy.real_portfolio_reviewer import load_latest_real_review

    _apply_ai_learning(tuning, load_latest_ai_learning())
    _apply_real_review(tuning, load_latest_real_review())
    _apply_midterm_tracker(tuning)

    if for_sim:
        tuning.strict_tag_filter = False
        tuning.require_ultra_tag_any = None

    tuning.ultra_min_score = int(max(35, min(65, tuning.ultra_min_score)))
    # 实盘扫描避免 AI 把门槛抬到 55+ 导致经常空榜
    if not for_sim and tuning.ultra_min_score > 48:
        tuning.notes.append(
            f"实盘扫描超短门槛由 {tuning.ultra_min_score} 软封顶至 48（避免空榜）"
        )
        tuning.ultra_min_score = 48
    tuning.midterm_min_score = int(max(58, min(72, tuning.midterm_min_score)))
    return tuning


def _tags_match_preferred(tags: str, preferred: Sequence[str]) -> bool:
    if not preferred:
        return False
    for tag in preferred:
        if tag in tags:
            return True
        for alias in ULTRA_STRONG_TAG_GROUPS.get(tag, []):
            if alias in tags:
                return True
    return False


def apply_ultra_tuning(item: dict, tuning: Optional[SelectionTuning]) -> Optional[dict]:
    """对单只超短标的应用复盘调优（评分加减 / 过滤）。"""
    if not tuning or not item:
        return item

    score = float(item.get("ultra_short_score", 0))
    tags = str(item.get("tags") or "")

    for tag, bonus in tuning.ultra_tag_bonus.items():
        if tag and tag in tags:
            score += bonus

    for tag, penalty in tuning.ultra_tag_penalty.items():
        if tag and tag in tags:
            score -= penalty

    gain_3d = float(item.get("gain_3d", 0) or 0)
    if tuning.ultra_penalize_3d_gain_above is not None and gain_3d > tuning.ultra_penalize_3d_gain_above:
        score -= 10
        if "高位追涨" not in tags:
            tags = tags + ",高位追涨" if tags else "高位追涨"

    pct = float(item.get("pct_chg", 0) or 0)
    sealed = bool(item.get("is_sealed_board"))
    chase_pct = tuning.ultra_penalize_unsealed_above_pct or tuning.ultra_penalize_pct_above
    if chase_pct is not None and pct > chase_pct and not sealed:
        score -= 8

    preferred = tuning.ultra_preferred_tags or []
    if preferred:
        if _tags_match_preferred(tags, preferred):
            score += 6
        else:
            score -= 3

    if tuning.require_ultra_tag_any and tuning.strict_tag_filter:
        if not _tags_match_preferred(tags, tuning.require_ultra_tag_any):
            return None

    item = dict(item)
    item["ultra_short_score"] = round(score, 1)
    item["tags"] = tags
    return item


def format_tuning_summary(tuning: SelectionTuning) -> str:
    if not tuning.notes:
        return (
            f"选股调优：超短≥{tuning.ultra_min_score} · 中线≥{tuning.midterm_min_score}"
            f"（暂无复盘样本，使用默认门槛）"
        )
    lines = [
        f"选股调优：超短≥{tuning.ultra_min_score} · 中线≥{tuning.midterm_min_score}",
        f"依据：{', '.join(tuning.sources)}",
    ]
    lines.extend(f"  · {n}" for n in tuning.notes[:6])
    return "\n".join(lines)
