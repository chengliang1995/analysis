"""根据模拟/实盘复盘记录，动态调整超短与中线选股参数。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from quantpy.paths import SIM_REVIEW_DIR, SIM_STATE_FILE

ULTRA_SHORT_STRATEGIES = frozenset({"超短", "涨停", "短线"})

# 调参最小样本：避免 2～3 笔噪声驱动门槛爬升
SIM_TUNING_MIN_TRADES = 15
SIM_TUNING_MIN_BUCKET = 8
SIM_TUNING_RECENT_WINDOW = 30

# 三倍量策略条件/标签（与中线分流，避免互相污染加减分）
TRIPLE_VOLUME_CONDITION_IDS = frozenset({
    "non_st", "vol_3x", "yang_line", "cross_ma5", "cross_ma10", "cross_ma20",
})
TRIPLE_VOLUME_TAGS = frozenset({"一阳穿三线"})
# 中线策略不应强化的标签（三倍量污染项）
MIDTERM_FORBIDDEN_TAG_BONUS = frozenset({"一阳穿三线"})
# 追涨类标签禁止加分（与「未封板追涨扣分」冲突）
MIDTERM_CHASE_TAG_BONUS_BLOCK = frozenset({
    "涨+10.0%", "涨+10%", "高位追涨", "当日偏热", "追涨",
})
# 旧底背离策略残留：选股已切 MA20 回踩，禁止再写入加减分
LEGACY_DIVERGENCE_CONDITION_IDS = frozenset({
    "diff_div", "obv_div", "price_new_low", "diff_below_zero", "vol_shrink",
    "rsi_div", "ma60_hold", "near_ma60", "stop_confirm", "entry_confirm",
    "not_freefall",
})
LEGACY_DIVERGENCE_TAGS = frozenset({
    "RSI底背离", "绿柱缩短", "MACD金叉", "MA60走平/向上", "MA60向上",
    "贴近MA60", "60分底背离", "MA60向下", "MA60走平", "等金叉确认",
    "偏远离MA60", "弱止跌确认", "DIFF底背离", "OBV底背离", "阶段新低",
})
# MA20 回踩硬筛/全员条件：无区分度，不加分
MA20_HARD_GATE_CONDITION_IDS = frozenset({
    "cap_range", "price_cap", "liquidity", "sector_ma20", "above_ma20",
    "ma20_breakout", "ma20_rising", "ma5_above_ma10", "ride_ma5",
    "pullback_ma5", "no_chase_rally",
})
# 当前中线可调优的条件白名单（仅差异化因子）
MA20_TUNABLE_CONDITION_IDS = frozenset({
    "vol_shrink_pullback",
})
MA20_TUNABLE_TAGS = frozenset({
    "回踩缩量", "均线多头",
})


def _is_legacy_midterm_condition(cond: str) -> bool:
    return str(cond or "") in LEGACY_DIVERGENCE_CONDITION_IDS


def _is_midterm_condition_tunable(cond: str) -> bool:
    c = str(cond or "")
    if not c or c in TRIPLE_VOLUME_CONDITION_IDS:
        return False
    if c in LEGACY_DIVERGENCE_CONDITION_IDS or c in MA20_HARD_GATE_CONDITION_IDS:
        return False
    return c in MA20_TUNABLE_CONDITION_IDS


def _is_midterm_tag_tunable(tag: str) -> bool:
    t = str(tag or "")
    if not t or t in TRIPLE_VOLUME_TAGS or t in MIDTERM_FORBIDDEN_TAG_BONUS:
        return False
    if t in LEGACY_DIVERGENCE_TAGS or _is_chase_tag(t):
        return False
    return t in MA20_TUNABLE_TAGS or t.startswith("MA20")

# 强势标签别名：复盘偏好「封板」时，连板/高换手等同类信号一并认可
ULTRA_STRONG_TAG_GROUPS: Dict[str, List[str]] = {
    "封板": ["封板", "强势封板", "涨停不破开", "连板"],
    "强势封板": ["强势封板", "封板", "涨停不破开", "连板"],
    "涨停不破开": ["涨停不破开", "强势封板", "封板", "连板"],
    "连板": ["连板", "封板", "强势封板"],
    "高换手": ["高换手", "放量"],
}


def _is_chase_tag(tag: str) -> bool:
    t = str(tag or "")
    if t in MIDTERM_CHASE_TAG_BONUS_BLOCK:
        return True
    if t.startswith("涨+") and "%" in t:
        try:
            pct = float(t.replace("涨+", "").replace("%", ""))
            return pct >= 8.0
        except ValueError:
            return True
    return False


@dataclass
class SelectionTuning:
    """选股调优参数（由复盘记录推导）。"""

    ultra_min_score: int = 35
    midterm_min_score: int = 62
    triple_min_score: int = 60
    # MA20 回踩独立门槛（勿与 midterm_min_score 混用）
    ma20_pullback_min_score: int = 62
    # 拒分档：如 [(70, 80)] 表示拒绝 [70,80)，保留 60–70 与 80+
    midterm_reject_score_bands: List = field(default_factory=list)
    ultra_tag_bonus: Dict[str, int] = field(default_factory=dict)
    ultra_tag_penalty: Dict[str, int] = field(default_factory=dict)
    ultra_penalize_3d_gain_above: Optional[float] = None
    ultra_penalize_pct_above: Optional[float] = None
    ultra_penalize_unsealed_above_pct: Optional[float] = None
    ultra_preferred_tags: Optional[List[str]] = None
    require_ultra_tag_any: Optional[List[str]] = None
    strict_tag_filter: bool = False
    # AI：中高分(约75-90)未封板时软降权，避免虚高分追涨
    ultra_demote_midhigh_unsealed: bool = False
    # 90+ 未封板：模拟/AI 显示虚高堆分胜率差
    ultra_demote_high_unsealed: bool = False
    midterm_ma20_chase_penalty: int = 0
    midterm_ma20_chase_ratio: float = 1.08
    midterm_penalize_ret_20d_below: Optional[float] = None
    midterm_condition_bonus: Dict[str, int] = field(default_factory=dict)
    midterm_condition_penalty: Dict[str, int] = field(default_factory=dict)
    midterm_tag_bonus: Dict[str, int] = field(default_factory=dict)
    midterm_tag_penalty: Dict[str, int] = field(default_factory=dict)
    triple_condition_bonus: Dict[str, int] = field(default_factory=dict)
    triple_condition_penalty: Dict[str, int] = field(default_factory=dict)
    triple_tag_bonus: Dict[str, int] = field(default_factory=dict)
    triple_tag_penalty: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _route_condition_maps(
    bonus: Optional[Dict[str, int]],
    penalty: Optional[Dict[str, int]],
) -> tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int]]:
    """将条件加减分拆到中线 / 三倍量；硬筛条件永不进入加减分（无区分度）。"""
    mid_b: Dict[str, int] = {}
    mid_p: Dict[str, int] = {}
    tri_b: Dict[str, int] = {}
    tri_p: Dict[str, int] = {}
    for key, val in (bonus or {}).items():
        if key in TRIPLE_VOLUME_CONDITION_IDS:
            continue  # 硬筛全员命中，加分空转
        if not _is_midterm_condition_tunable(key):
            continue
        mid_b[key] = int(val)
    for key, val in (penalty or {}).items():
        if key in TRIPLE_VOLUME_CONDITION_IDS:
            continue
        if _is_legacy_midterm_condition(key) or key in MA20_HARD_GATE_CONDITION_IDS:
            continue
        if key in MA20_TUNABLE_CONDITION_IDS:
            mid_p[key] = int(val)
    return mid_b, mid_p, tri_b, tri_p


def _route_tag_maps(
    bonus: Optional[Dict[str, int]],
    penalty: Optional[Dict[str, int]],
) -> tuple[Dict[str, int], Dict[str, int], Dict[str, int], Dict[str, int]]:
    """将标签加减分拆到中线 / 三倍量；过滤旧背离/追涨/硬筛 bonus。"""
    mid_b: Dict[str, int] = {}
    mid_p: Dict[str, int] = {}
    tri_b: Dict[str, int] = {}
    tri_p: Dict[str, int] = {}
    for key, val in (bonus or {}).items():
        if key in TRIPLE_VOLUME_TAGS:
            continue
        if key in MIDTERM_FORBIDDEN_TAG_BONUS:
            continue
        if _is_chase_tag(key) or key in LEGACY_DIVERGENCE_TAGS:
            continue
        if not _is_midterm_tag_tunable(key):
            continue
        mid_b[key] = int(val)
    for key, val in (penalty or {}).items():
        if key in TRIPLE_VOLUME_TAGS or key in LEGACY_DIVERGENCE_TAGS:
            continue
        # 仅保留对当前策略有意义的降权（如当日偏热）
        if key in MA20_TUNABLE_TAGS or _is_chase_tag(key) or key == "当日偏热":
            mid_p[key] = int(val)
    return mid_b, mid_p, tri_b, tri_p


def _merge_int_map(target: Dict[str, int], src: Dict[str, int]) -> None:
    for key, val in src.items():
        target[key] = max(target.get(key, 0), int(val))


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


def _load_sim_midterm_trades() -> pd.DataFrame:
    if not SIM_STATE_FILE.exists():
        return pd.DataFrame()
    try:
        state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
        trades = (state.get("midterm") or {}).get("closed_trades") or []
    except (OSError, json.JSONDecodeError):
        return pd.DataFrame()
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    if "profit_pct" not in df.columns:
        return pd.DataFrame()
    return df


def _apply_sim_midterm_insights(tuning: SelectionTuning, df: pd.DataFrame) -> None:
    if df.empty or len(df) < 3:
        return
    score_col = "midterm_score" if "midterm_score" in df.columns else "score"
    if score_col not in df.columns:
        return
    tuning.sources.append(f"sim_midterm(n={len(df)})")
    for low, high, label in [(0, 65, "<65"), (65, 80, "65-80"), (80, 999, "80+")]:
        part = df[(df[score_col] >= low) & (df[score_col] < high)]
        if part.empty:
            continue
        wr = (part["profit_pct"] > 0).mean() * 100
        if label == "80+" and len(part) >= 3 and wr >= 55:
            # 强化高分偏好，但不一刀砍掉 60–70
            tuning.midterm_min_score = max(tuning.midterm_min_score, 62)
            tuning.notes.append(f"模拟中线80+胜率{wr:.0f}%，偏好高分并保留60–70档")
        if label == "65-80" and len(part) >= SIM_TUNING_MIN_BUCKET and wr < 40:
            _set_reject_band(tuning, 70, 80)
            tuning.notes.append(f"模拟中线65-80胜率{wr:.0f}%偏弱，拒分档70–80")


def _set_reject_band(tuning: SelectionTuning, lo: float, hi: float) -> None:
    bands = list(tuning.midterm_reject_score_bands or [])
    key = (float(lo), float(hi))
    if key not in {(float(b[0]), float(b[1])) for b in bands if isinstance(b, (list, tuple)) and len(b) >= 2}:
        bands.append([float(lo), float(hi)])
    tuning.midterm_reject_score_bands = bands


def _apply_sim_trade_insights(tuning: SelectionTuning, df: pd.DataFrame) -> None:
    if df.empty or len(df) < SIM_TUNING_MIN_TRADES:
        if not df.empty:
            tuning.notes.append(
                f"模拟平仓仅 {len(df)} 笔(<{SIM_TUNING_MIN_TRADES})，跳过超短自动调参"
            )
        return

    win_rate = (df["profit_pct"] > 0).mean() * 100
    avg_profit = float(df["profit_pct"].mean())
    tuning.sources.append(f"sim_trades(n={len(df)})")

    recent = df
    if "sell_date" in df.columns:
        recent = df.sort_values("sell_date").tail(SIM_TUNING_RECENT_WINDOW)
    recent_wr = (recent["profit_pct"] > 0).mean() * 100 if len(recent) >= SIM_TUNING_MIN_TRADES else win_rate
    stop_rate = (
        recent["exit_reason"].astype(str).str.contains("止损", na=False).mean() * 100
        if len(recent) >= SIM_TUNING_MIN_TRADES and "exit_reason" in recent.columns else 0
    )

    if len(recent) >= SIM_TUNING_MIN_TRADES and recent_wr >= 52 and avg_profit > 0.5:
        old = tuning.ultra_min_score
        tuning.ultra_min_score = max(35, old - 2)
        if tuning.ultra_min_score < old:
            tuning.notes.append(
                f"近{len(recent)}笔胜率{recent_wr:.0f}%/均益{avg_profit:+.2f}%，门槛 {old}→{tuning.ultra_min_score}"
            )

    if len(recent) >= SIM_TUNING_MIN_TRADES and (recent_wr < 38 or stop_rate >= 60):
        tuning.ultra_min_score = max(tuning.ultra_min_score, 48)
        tuning.ultra_penalize_unsealed_above_pct = min(
            tuning.ultra_penalize_unsealed_above_pct or 99, 5.0,
        )
        tuning.ultra_demote_midhigh_unsealed = True
        tuning.ultra_demote_high_unsealed = True
        tuning.strict_tag_filter = True
        tuning.require_ultra_tag_any = [
            "强势封板", "封板", "涨停不破开", "连板",
        ]
        tuning.ultra_preferred_tags = ["涨停不破开", "强势封板", "封板", "连板"]
        tuning.notes.append(
            f"近{len(recent)}笔胜率{recent_wr:.0f}%/止损{stop_rate:.0f}%，启用封板硬筛+门槛≥48"
        )

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
        if row["bucket"] == "90+" and row["count"] >= 5 and row["win_rate"] < 45:
            tuning.ultra_demote_high_unsealed = True
            tuning.ultra_penalize_unsealed_above_pct = min(
                tuning.ultra_penalize_unsealed_above_pct or 99, 5.0,
            )
            tuning.ultra_preferred_tags = ["涨停不破开", "强势封板", "封板", "连板"]
            tuning.notes.append(
                f"评分90+胜率仅{row['win_rate']:.0f}%，抑制未封板超高分"
            )
        if row["bucket"] == "60-75" and row["count"] >= 5 and row["win_rate"] < 42:
            tuning.ultra_min_score = max(tuning.ultra_min_score, 43)
            tuning.notes.append(f"评分60-75胜率{row['win_rate']:.0f}%偏低，略抬超短门槛")
        if row["bucket"] == "75-90" and row["count"] >= 5 and row["win_rate"] < 40:
            tuning.ultra_demote_midhigh_unsealed = True
            tuning.ultra_penalize_unsealed_above_pct = min(
                tuning.ultra_penalize_unsealed_above_pct or 99, 5.5,
            )
            tuning.ultra_penalize_3d_gain_above = min(
                tuning.ultra_penalize_3d_gain_above or 99, 18.0,
            )
            tuning.ultra_preferred_tags = ["涨停不破开", "强势封板", "封板", "连板"]
            tuning.ultra_tag_bonus.setdefault("强势封板", 6)
            tuning.ultra_tag_bonus.setdefault("涨停不破开", 5)
            tuning.notes.append(
                f"评分75-90胜率仅{row['win_rate']:.0f}%，抑制未封板中高分追涨"
            )

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
        suggested = int(sel["midterm_min_score"])
        if suggested >= 78:
            tuning.midterm_min_score = max(tuning.midterm_min_score, 62)
            _set_reject_band(tuning, 70, 80)
        else:
            tuning.midterm_min_score = max(tuning.midterm_min_score, suggested)
    if sel.get("midterm_reject_score_bands"):
        for band in sel["midterm_reject_score_bands"]:
            if isinstance(band, (list, tuple)) and len(band) >= 2:
                _set_reject_band(tuning, band[0], band[1])
    if sel.get("triple_min_score") is not None:
        tuning.triple_min_score = max(tuning.triple_min_score, int(sel["triple_min_score"]))

    mid_b, mid_p, tri_b, tri_p = _route_condition_maps(
        sel.get("midterm_condition_bonus"),
        sel.get("midterm_condition_penalty"),
    )
    # 显式 triple_* 优先合并
    _, _, tri_b2, tri_p2 = _route_condition_maps(
        sel.get("triple_condition_bonus"),
        sel.get("triple_condition_penalty"),
    )
    tri_b.update(tri_b2)
    tri_p.update(tri_p2)
    _merge_int_map(tuning.midterm_condition_bonus, mid_b)
    _merge_int_map(tuning.midterm_condition_penalty, mid_p)
    _merge_int_map(tuning.triple_condition_bonus, tri_b)
    _merge_int_map(tuning.triple_condition_penalty, tri_p)

    mid_tb, mid_tp, tri_tb, tri_tp = _route_tag_maps(
        sel.get("midterm_tag_bonus"),
        sel.get("midterm_tag_penalty"),
    )
    _, _, tri_tb2, tri_tp2 = _route_tag_maps(
        sel.get("triple_tag_bonus"),
        sel.get("triple_tag_penalty"),
    )
    tri_tb.update(tri_tb2)
    tri_tp.update(tri_tp2)
    _merge_int_map(tuning.midterm_tag_bonus, mid_tb)
    _merge_int_map(tuning.midterm_tag_penalty, mid_tp)
    _merge_int_map(tuning.triple_tag_bonus, tri_tb)
    _merge_int_map(tuning.triple_tag_penalty, tri_tp)

    if tuning.triple_condition_penalty or any(
        t in TRIPLE_VOLUME_TAGS for t in tuning.triple_tag_penalty
    ):
        tuning.triple_min_score = max(tuning.triple_min_score, 68)
        tuning.triple_condition_penalty = {
            k: v for k, v in tuning.triple_condition_penalty.items()
            if k not in TRIPLE_VOLUME_CONDITION_IDS
        }
        tuning.triple_tag_penalty = {
            k: v for k, v in tuning.triple_tag_penalty.items()
            if k not in TRIPLE_VOLUME_TAGS
        }

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
    for tag, penalty in (sel.get("ultra_tag_penalty") or {}).items():
        tuning.ultra_tag_penalty[tag] = max(tuning.ultra_tag_penalty.get(tag, 0), int(penalty))
    if sel.get("ultra_demote_midhigh_unsealed"):
        tuning.ultra_demote_midhigh_unsealed = True
    if sel.get("ultra_demote_high_unsealed"):
        tuning.ultra_demote_high_unsealed = True

    analytics = ai.get("analytics") or ai.get("analytics_summary") or {}
    primary = analytics if analytics.get("sufficient") else analytics.get("real") or {}
    if primary.get("sufficient"):
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
        tuning.midterm_min_score = max(tuning.midterm_min_score, 62)
    elif midterm.get("sufficient") and float(midterm.get("win_rate", 100)) >= 55:
        if sel.get("midterm_min_score") is not None:
            suggested = int(sel["midterm_min_score"])
            if suggested >= 78:
                tuning.midterm_min_score = max(tuning.midterm_min_score, 62)
                _set_reject_band(tuning, 70, 80)
            else:
                tuning.midterm_min_score = max(tuning.midterm_min_score, suggested)
    if sel.get("midterm_reject_score_bands"):
        for band in sel["midterm_reject_score_bands"]:
            if isinstance(band, (list, tuple)) and len(band) >= 2:
                _set_reject_band(tuning, band[0], band[1])

    # 旧底背离文案不再写入加减分；仅保留 MA20 差异化因子提示
    for sug in ai.get("suggestions") or []:
        text = str(sug)
        if "回踩缩量" in text and "强化" in text:
            tuning.midterm_condition_bonus["vol_shrink_pullback"] = max(
                tuning.midterm_condition_bonus.get("vol_shrink_pullback", 0), 6,
            )
            tuning.midterm_tag_bonus["回踩缩量"] = max(
                tuning.midterm_tag_bonus.get("回踩缩量", 0), 5,
            )
        if "均线多头" in text and "强化" in text:
            tuning.midterm_tag_bonus["均线多头"] = max(
                tuning.midterm_tag_bonus.get("均线多头", 0), 4,
            )


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
        mid_b, mid_p, tri_b, tri_p = _route_condition_maps(
            {
                k: v for k, v in (factor.get("midterm_condition_bonus") or {}).items()
                if int(v) >= 0
            },
            {
                **(factor.get("midterm_condition_penalty") or {}),
                **{
                    k: abs(int(v))
                    for k, v in (factor.get("midterm_condition_bonus") or {}).items()
                    if int(v) < 0
                },
            },
        )
        _merge_int_map(tuning.midterm_condition_bonus, mid_b)
        _merge_int_map(tuning.midterm_condition_penalty, mid_p)
        _merge_int_map(tuning.triple_condition_bonus, tri_b)
        _merge_int_map(tuning.triple_condition_penalty, tri_p)

        mid_tb, mid_tp, tri_tb, tri_tp = _route_tag_maps(
            {
                k: v for k, v in (factor.get("midterm_tag_bonus") or {}).items()
                if int(v) >= 0
            },
            {
                **(factor.get("midterm_tag_penalty") or {}),
                **{
                    k: abs(int(v))
                    for k, v in (factor.get("midterm_tag_bonus") or {}).items()
                    if int(v) < 0
                },
            },
        )
        _merge_int_map(tuning.midterm_tag_bonus, mid_tb)
        _merge_int_map(tuning.midterm_tag_penalty, mid_tp)
        _merge_int_map(tuning.triple_tag_bonus, tri_tb)
        _merge_int_map(tuning.triple_tag_penalty, tri_tp)

        if factor.get("midterm_min_score") is not None:
            # 跟进给出的 ≥80 改写为拒弱档，避免误杀 60–70
            suggested = int(factor["midterm_min_score"])
            if suggested >= 78:
                tuning.midterm_min_score = max(tuning.midterm_min_score, 62)
                _set_reject_band(tuning, 70, 80)
                tuning.notes.append("跟进建议门槛≥80 → 改为拒分档70–80，底线≥62")
            else:
                tuning.midterm_min_score = max(tuning.midterm_min_score, suggested)
        if factor.get("midterm_reject_score_bands"):
            for band in factor["midterm_reject_score_bands"]:
                if isinstance(band, (list, tuple)) and len(band) >= 2:
                    _set_reject_band(tuning, band[0], band[1])
        if factor.get("triple_min_score") is not None:
            tuning.triple_min_score = max(
                tuning.triple_min_score, int(factor["triple_min_score"]),
            )
        # 三倍量：仅抬门槛，硬筛条件加减分一律清空
        tuning.triple_condition_bonus = {}
        tuning.triple_condition_penalty = {}
        tuning.triple_tag_bonus = {
            k: v for k, v in tuning.triple_tag_bonus.items()
            if k not in TRIPLE_VOLUME_TAGS
        }
        tuning.triple_tag_penalty = {
            k: v for k, v in tuning.triple_tag_penalty.items()
            if k not in TRIPLE_VOLUME_TAGS
        }
        if factor.get("triple_min_score") or any(
            "三倍量" in str(n) for n in (factor.get("notes") or [])
        ):
            tuning.triple_min_score = max(tuning.triple_min_score, 68)
            tuning.notes.append("三倍量跟进偏弱或硬筛空转，仅抬门槛≥68（不逐项加分）")
        # 清洗追涨标签加分
        tuning.midterm_tag_bonus = {
            k: v for k, v in tuning.midterm_tag_bonus.items() if not _is_chase_tag(k)
        }
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
        # 成熟跟进：80+ 优于 70-80 → 拒弱档而非一刀 ≥80
        for row in summary.get("by_score_bucket", []):
            if row.get("key") == "80+" and row.get("count", 0) >= 8:
                high_wr = float(row.get("win_rate", 0))
                low_row = next(
                    (r for r in summary.get("by_score_bucket", []) if r.get("key") == "70-80"),
                    {},
                )
                low_wr = float(low_row.get("win_rate", 100))
                if high_wr >= 55 and low_wr < 45:
                    tuning.midterm_min_score = max(tuning.midterm_min_score, 62)
                    _set_reject_band(tuning, 70, 80)
                    tuning.notes.append(
                        f"跟进80+胜率{high_wr:.0f}% vs 70-80仅{low_wr:.0f}%，拒分档70–80"
                    )
    except Exception as exc:
        tuning.notes.append(f"中线跟进调优读取失败: {type(exc).__name__}")


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
        if name not in ULTRA_SHORT_STRATEGIES:
            wr = float(strat.get("win_rate", 100))
            avg_p = float(strat.get("avg_profit", 0))
            if wr < 50 and avg_p < 0 and int(strat.get("count") or 0) >= 5:
                tuning.midterm_min_score = max(tuning.midterm_min_score, 68)
                tuning.midterm_penalize_ret_20d_below = -6.0
                tuning.notes.append(
                    f"实盘「{name}」胜率 {wr}% / 均收益 {avg_p:+.2f}%，收紧中线评分"
                )
            continue
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
    _apply_sim_midterm_insights(tuning, _load_sim_midterm_trades())

    review_stats = _load_latest_sim_review_stats()
    wr = float(review_stats.get("win_rate", 100) or 100)
    if wr < 40:
        tuning.ultra_min_score = max(tuning.ultra_min_score, 48)
        tuning.notes.append(f"模拟复盘胜率 {wr:.0f}% 偏低，超短门槛≥48")
    elif wr < 45:
        tuning.ultra_min_score = max(tuning.ultra_min_score, 40)

    from quantpy.ai_learning_optimizer import load_latest_ai_learning
    from quantpy.real_portfolio_reviewer import load_latest_real_review

    _apply_ai_learning(tuning, load_latest_ai_learning())
    _apply_real_review(tuning, load_latest_real_review())
    _apply_midterm_tracker(tuning)

    if for_sim:
        sim_df = _load_sim_closed_trades()
        if not sim_df.empty and "sell_date" in sim_df.columns:
            recent = sim_df.sort_values("sell_date").tail(30)
            if len(recent) >= 8 and (recent["profit_pct"] > 0).mean() < 0.38:
                tuning.strict_tag_filter = True
                tuning.require_ultra_tag_any = tuning.require_ultra_tag_any or [
                    "强势封板", "封板", "涨停不破开", "连板",
                ]
                tuning.ultra_min_score = max(tuning.ultra_min_score, 48)
        if not tuning.strict_tag_filter:
            tuning.require_ultra_tag_any = None

    tuning.ultra_min_score = int(max(35, min(65, tuning.ultra_min_score)))
    # 实盘与模拟口径对齐：软封顶 55（不再压到 48 抵消 AI 收紧）
    if not for_sim and tuning.ultra_min_score > 55:
        tuning.notes.append(
            f"实盘扫描超短门槛由 {tuning.ultra_min_score} 软封顶至 55"
        )
        tuning.ultra_min_score = 55
    tuning.midterm_min_score = int(max(58, min(72, tuning.midterm_min_score)))
    # MA20 独立门槛：不低于中线底线，但不受中线误抬到 68+ 绑架
    ma20_floor = int(getattr(tuning, "ma20_pullback_min_score", 62) or 62)
    ma20_floor = max(62, min(70, ma20_floor, tuning.midterm_min_score))
    tuning.ma20_pullback_min_score = int(max(58, min(72, ma20_floor)))
    tuning.triple_min_score = int(max(55, min(80, tuning.triple_min_score)))
    # 清理空转的硬筛加减分、旧背离与追涨标签
    tuning.triple_condition_bonus = {}
    tuning.triple_condition_penalty = {}
    tuning.triple_tag_bonus = {
        k: v for k, v in (tuning.triple_tag_bonus or {}).items()
        if k not in TRIPLE_VOLUME_TAGS
    }
    tuning.midterm_condition_bonus = {
        k: v for k, v in (tuning.midterm_condition_bonus or {}).items()
        if _is_midterm_condition_tunable(k)
    }
    tuning.midterm_condition_penalty = {
        k: v for k, v in (tuning.midterm_condition_penalty or {}).items()
        if k in MA20_TUNABLE_CONDITION_IDS
    }
    tuning.midterm_tag_bonus = {
        k: v for k, v in (tuning.midterm_tag_bonus or {}).items()
        if _is_midterm_tag_tunable(k)
    }
    tuning.midterm_tag_penalty = {
        k: v for k, v in (tuning.midterm_tag_penalty or {}).items()
        if k not in LEGACY_DIVERGENCE_TAGS
    }
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
        score -= 15
        if pct >= 7.0 and tuning.strict_tag_filter:
            return None

    preferred = tuning.ultra_preferred_tags or []
    if preferred:
        if _tags_match_preferred(tags, preferred):
            score += 6
        else:
            score -= 3

    # 中高分未封板：AI 显示 75-90 档常因追涨虚高而胜率差
    if tuning.ultra_demote_midhigh_unsealed and not sealed and 72 <= score < 90:
        score -= 8
        if "中高分未封板" not in tags:
            tags = tags + ",中高分未封板" if tags else "中高分未封板"

    if tuning.ultra_demote_high_unsealed and not sealed and score >= 88:
        score -= 10
        if "超高分未封板" not in tags:
            tags = tags + ",超高分未封板" if tags else "超高分未封板"

    if tuning.strict_tag_filter and tuning.require_ultra_tag_any:
        if not _tags_match_preferred(tags, tuning.require_ultra_tag_any):
            return None

    if score < tuning.ultra_min_score:
        return None

    item = dict(item)
    item["ultra_short_score"] = round(score, 1)
    item["tags"] = tags
    return item


def apply_triple_tuning(item: dict, tuning: Optional[SelectionTuning]) -> Optional[dict]:
    """对单只三倍量标的应用 AI/跟进调优。

    硬筛条件（vol_3x/阳线/穿三线等）全员命中，无区分度，不逐项加减分；
    仅用 triple_min_score 门槛过滤，并对差异化标签做微调。
    """
    if not tuning or not item:
        return item

    score = float(item.get("midterm_score", 0) or 0)
    tags = str(item.get("tags") or "")
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if item.get("condition_labels"):
        tag_list = list(dict.fromkeys(tag_list + list(item["condition_labels"])))

    # 仅对非硬筛标签微调（排除「一阳穿三线」等全员标签）
    for tag_key, bonus in tuning.triple_tag_bonus.items():
        if tag_key in TRIPLE_VOLUME_TAGS:
            continue
        if any(tag_key in t or t == tag_key for t in tag_list):
            score += bonus
    for tag_key, penalty in tuning.triple_tag_penalty.items():
        if tag_key in TRIPLE_VOLUME_TAGS:
            continue
        if any(tag_key in t or t == tag_key for t in tag_list):
            score -= penalty

    from quantpy.midterm_triple_volume_selector import is_triple_pct_chase_risky

    vol_ratio = float(item.get("volume_ratio") or 0)
    pct = float(item.get("pct_chg") or 0)
    if is_triple_pct_chase_risky(pct):
        return None
    if vol_ratio >= 5:
        score += 3
    elif vol_ratio >= 4:
        score += 1
    elif pct >= 5 and vol_ratio < 4:
        score -= 3

    if score < tuning.triple_min_score:
        return None

    item = dict(item)
    item["midterm_score"] = round(min(score, 99.0), 1)
    return item


def format_tuning_summary(tuning: SelectionTuning) -> str:
    if not tuning.notes and not tuning.sources:
        return (
            f"选股调优：超短≥{tuning.ultra_min_score} · 中线≥{tuning.midterm_min_score}"
            f" · MA20回踩≥{tuning.ma20_pullback_min_score}"
            f" · 三倍量≥{tuning.triple_min_score}"
            f"（暂无复盘样本，使用默认门槛）"
        )
    lines = [
        f"选股调优：超短≥{tuning.ultra_min_score} · 中线≥{tuning.midterm_min_score}"
        f" · MA20回踩≥{tuning.ma20_pullback_min_score}"
        f" · 三倍量≥{tuning.triple_min_score}",
    ]
    if tuning.midterm_reject_score_bands:
        bands = ",".join(
            f"[{b[0]:g},{b[1]:g})"
            for b in tuning.midterm_reject_score_bands
            if isinstance(b, (list, tuple)) and len(b) >= 2
        )
        lines.append(f"拒分档：{bands}")
    if tuning.sources:
        lines.append(f"依据：{', '.join(tuning.sources)}")
    lines.extend(f"  · {n}" for n in tuning.notes[:6])
    return "\n".join(lines)
