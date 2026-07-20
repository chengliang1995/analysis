"""
中线选股跟进：每日记录 TOP10 推荐，持续跟踪 10 个交易日，
统计胜率与因子表现，并反馈到选股调优 / AI 学习。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from quantpy.json_util import sanitize_for_json
from quantpy.paths import MIDTERM_TRACKER_FILE
from quantpy.stock_data import get_stock_hist

TOP_N_DEFAULT = 10
FOLLOW_TRADING_DAYS = 10
WIN_THRESHOLD_PCT = 3.0
MIN_SAMPLES_FOR_FACTOR = 3
INTERIM_MIN_SAMPLES = 5
INTERIM_MIN_HOLD_DAYS = 2


def _default_state() -> dict:
    return {
        "version": 1,
        "follow_trading_days": FOLLOW_TRADING_DAYS,
        "win_threshold_pct": WIN_THRESHOLD_PCT,
        "top_n": TOP_N_DEFAULT,
        "records": [],
        "daily_batches": [],
        "last_record_date": "",
        "last_evaluated": "",
        "summary": {},
    }


def _load_state() -> dict:
    if not MIDTERM_TRACKER_FILE.exists():
        return _default_state()
    try:
        data = json.loads(MIDTERM_TRACKER_FILE.read_text(encoding="utf-8"))
        base = _default_state()
        base.update(data)
        # 始终同步当前跟进周期（缩短周期后需重评到期）
        base["follow_trading_days"] = FOLLOW_TRADING_DAYS
        return base
    except (OSError, json.JSONDecodeError):
        return _default_state()


def _save_state(state: dict) -> None:
    MIDTERM_TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    MIDTERM_TRACKER_FILE.write_text(
        json.dumps(sanitize_for_json(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _normalize_hist_dates(df: pd.DataFrame) -> pd.DataFrame:
    """统一历史 K 线日期列为 YYYY-MM-DD 字符串的 date。"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "date" not in out.columns:
        for col in ("日期", "day", "time", "datetime", "trade_date"):
            if col in out.columns:
                out["date"] = out[col]
                break
        else:
            if isinstance(out.index, pd.DatetimeIndex):
                out = out.reset_index()
                first = out.columns[0]
                out = out.rename(columns={first: "date"})
            elif out.index.name in ("date", "日期", "day", "time"):
                out = out.reset_index().rename(columns={out.index.name: "date"})
            else:
                return pd.DataFrame()
    try:
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, AttributeError):
        return pd.DataFrame()
    out = out.dropna(subset=["date"])
    return out


def _trading_days_between(start: str, end: str) -> List[str]:
    """获取 [start, end] 区间交易日列表（含两端）。"""
    try:
        ref = get_stock_hist("000001", days=400, patch_live=False)
        ref = _normalize_hist_dates(ref)
    except Exception:
        ref = pd.DataFrame()
    if ref.empty:
        # 行情源异常时，用工作日近似（10 交易日 ≈ 14 自然日）
        try:
            days = pd.bdate_range(start=start, end=end).strftime("%Y-%m-%d").tolist()
        except (ValueError, TypeError):
            return []
        return days
    days = ref["date"].tolist()
    if start not in days:
        days = [d for d in days if d >= start]
    else:
        idx = days.index(start)
        days = days[idx:]
    return [d for d in days if d <= end]


def _mature_date(pick_date: str, follow_days: int = FOLLOW_TRADING_DAYS) -> Optional[str]:
    if not pick_date:
        return None
    cal = _trading_days_between(pick_date, _today())
    if len(cal) <= follow_days:
        return None
    return cal[follow_days]


def _pick_snapshot(rec: dict, rank: int, pick_date: str) -> dict:
    tags = str(rec.get("tags") or "")
    return {
        "pick_date": pick_date,
        "rank": rank,
        "code": str(rec.get("code", "")).zfill(6),
        "name": str(rec.get("name", "")),
        "pick_price": float(rec.get("price") or 0),
        "midterm_score": float(rec.get("midterm_score") or 0),
        "conditions": list(rec.get("conditions") or []),
        "condition_labels": list(rec.get("condition_labels") or []),
        "tags": tags,
        "tag_list": [t.strip() for t in tags.split(",") if t.strip()],
        "trend": rec.get("trend", ""),
        "hold_style": rec.get("hold_style", ""),
        "rsi": rec.get("rsi"),
        "ret_20d": rec.get("ret_20d"),
        "ma60_trend": rec.get("ma60_trend", ""),
        "bottom_divergence": bool(rec.get("bottom_divergence")),
        "pe": rec.get("pe"),
        "profit_yoy": rec.get("profit_yoy"),
        "industry": rec.get("industry", ""),
        "status": "tracking",
        "mature_date": "",
        "mature_price": None,
        "return_pct": None,
        "max_return_pct": None,
        "is_win": None,
        "evaluated_at": "",
    }


def record_midterm_top_picks(
    recommendations: List[dict],
    *,
    top_n: int = TOP_N_DEFAULT,
    pick_date: Optional[str] = None,
    show_progress: bool = False,
) -> dict:
    """记录当日中线推荐 TOP N，用于 1 个月跟进。"""
    if not recommendations:
        return {"recorded": 0, "message": "无推荐标的"}

    pick_date = pick_date or _today()
    state = _load_state()
    existing_keys = {
        (r["pick_date"], r["code"])
        for r in state.get("records", [])
    }

    top = recommendations[:top_n]
    new_records: List[dict] = []
    for i, rec in enumerate(top, 1):
        code = str(rec.get("code", "")).zfill(6)
        if not code or code == "000000":
            continue
        if (pick_date, code) in existing_keys:
            continue
        if float(rec.get("price") or 0) <= 0:
            continue
        new_records.append(_pick_snapshot(rec, i, pick_date))

    if not new_records:
        if show_progress:
            print(f"  跟进池：{pick_date} 已记录或无有效 TOP{top_n}")
        return {"recorded": 0, "pick_date": pick_date, "message": "当日已记录"}

    state.setdefault("records", []).extend(new_records)
    batches = state.setdefault("daily_batches", [])
    batches = [b for b in batches if b.get("date") != pick_date]
    batches.append({
        "date": pick_date,
        "count": len(new_records),
        "codes": [r["code"] for r in new_records],
    })
    state["daily_batches"] = batches[-90:]
    state["records"] = state["records"][-500:]
    state["last_record_date"] = pick_date
    state["summary"] = compute_tracker_summary(state)
    _save_state(state)

    if show_progress:
        names = "、".join(r["name"] for r in new_records[:5])
        print(f"  跟进池：记录 TOP{len(new_records)}（{pick_date}）{names}")

    return {
        "recorded": len(new_records),
        "pick_date": pick_date,
        "codes": [r["code"] for r in new_records],
    }


def evaluate_matured_picks(
    *,
    show_progress: bool = False,
    force: bool = False,
) -> dict:
    """评估已满跟进期的选股（默认 10 交易日）。"""
    state = _load_state()
    follow_days = FOLLOW_TRADING_DAYS
    state["follow_trading_days"] = follow_days
    win_th = float(state.get("win_threshold_pct", WIN_THRESHOLD_PCT))
    today = _today()
    updated = 0

    for rec in state.get("records", []):
        if rec.get("status") == "matured" and not force:
            continue
        if rec.get("status") not in ("tracking", "matured"):
            continue

        pick_date = rec.get("pick_date", "")
        mature_day = _mature_date(pick_date, follow_days)
        if not mature_day:
            continue

        code = rec.get("code", "")
        pick_price = float(rec.get("pick_price") or 0)
        if pick_price <= 0:
            rec["status"] = "error"
            continue

        try:
            hist = _normalize_hist_dates(get_stock_hist(code, days=120, patch_live=False))
        except Exception:
            continue
        if hist.empty or "close" not in hist.columns:
            continue
        window = hist[(hist["date"] >= pick_date) & (hist["date"] <= mature_day)]
        if window.empty:
            continue

        mature_row = window[window["date"] == mature_day]
        if mature_row.empty:
            mature_row = window.iloc[[-1]]
        close = pd.to_numeric(mature_row["close"], errors="coerce")
        if close.isna().all():
            continue
        mature_price = float(close.iloc[-1])
        ret_pct = (mature_price - pick_price) / pick_price * 100

        highs = pd.to_numeric(window["high"], errors="coerce") if "high" in window.columns else pd.Series(dtype=float)
        max_high = float(highs.max()) if highs.notna().any() else mature_price
        max_ret = (max_high - pick_price) / pick_price * 100

        rec["status"] = "matured"
        rec["mature_date"] = mature_day
        rec["mature_price"] = round(mature_price, 2)
        rec["return_pct"] = round(ret_pct, 2)
        rec["max_return_pct"] = round(max_ret, 2)
        rec["is_win"] = ret_pct >= win_th
        rec["is_positive"] = ret_pct > 0
        rec["evaluated_at"] = datetime.now().isoformat()
        updated += 1

    state["last_evaluated"] = today
    state["summary"] = compute_tracker_summary(state)
    try:
        state["interim_summary"] = compute_interim_summary(state)
        state["interim_computed_at"] = today
    except Exception:
        pass
    _save_state(state)

    if show_progress and updated:
        s = state["summary"]
        print(
            f"  跟进评估：更新 {updated} 只，"
            f"累计成熟 {s.get('matured_count', 0)}，"
            f"胜率 {s.get('win_rate', 0)}%"
        )

    return {"updated": updated, "summary": state["summary"]}


def compute_tracker_summary(state: Optional[dict] = None) -> dict:
    state = state or _load_state()
    records = state.get("records", [])
    win_th = float(state.get("win_threshold_pct", WIN_THRESHOLD_PCT))
    matured = [r for r in records if r.get("status") == "matured"]
    tracking = [r for r in records if r.get("status") == "tracking"]

    if not matured:
        return {
            "total_records": len(records),
            "tracking_count": len(tracking),
            "matured_count": 0,
            "win_rate": 0.0,
            "positive_rate": 0.0,
            "avg_return": 0.0,
            "avg_max_return": 0.0,
            "win_threshold_pct": win_th,
            "follow_trading_days": state.get("follow_trading_days", FOLLOW_TRADING_DAYS),
            "by_condition": [],
            "by_tag": [],
            "by_score_bucket": [],
            "by_ma60_trend": [],
            "factor_insights": [],
        }

    wins = [r for r in matured if r.get("is_win")]
    positive = [r for r in matured if float(r.get("return_pct") or 0) > 0]
    rets = [float(r["return_pct"]) for r in matured if r.get("return_pct") is not None]
    max_rets = [float(r["max_return_pct"]) for r in matured if r.get("max_return_pct") is not None]

    by_condition = _group_factor_stats(matured, "conditions")
    by_tag = _group_tag_stats(matured)
    by_score = _group_score_stats(matured)
    by_ma60 = _group_key_stats(matured, "ma60_trend")
    insights = _build_factor_insights(by_condition, by_tag, by_score, by_ma60)

    return {
        "total_records": len(records),
        "tracking_count": len(tracking),
        "matured_count": len(matured),
        "win_count": len(wins),
        "win_rate": round(len(wins) / len(matured) * 100, 1),
        "positive_rate": round(len(positive) / len(matured) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2) if rets else 0.0,
        "avg_max_return": round(sum(max_rets) / len(max_rets), 2) if max_rets else 0.0,
        "win_threshold_pct": win_th,
        "follow_trading_days": state.get("follow_trading_days", FOLLOW_TRADING_DAYS),
        "by_condition": by_condition,
        "by_tag": by_tag,
        "by_score_bucket": by_score,
        "by_ma60_trend": by_ma60,
        "factor_insights": insights,
        "last_batch_date": state.get("last_record_date", ""),
        "updated_at": datetime.now().isoformat(),
    }


def _group_factor_stats(records: List[dict], field: str) -> List[dict]:
    from quantpy.midterm_portfolio_advisor import _CONDITION_LABELS
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        for key in r.get(field) or []:
            buckets[str(key)].append(r)
    rows = _stats_from_buckets(buckets)
    for row in rows:
        row["label"] = _CONDITION_LABELS.get(row["key"], row["key"])
    return rows


def _group_tag_stats(records: List[dict]) -> List[dict]:
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        for tag in r.get("tag_list") or []:
            buckets[tag].append(r)
    return _stats_from_buckets(buckets)


def _group_score_stats(records: List[dict]) -> List[dict]:
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        score = float(r.get("midterm_score") or 0)
        if score >= 80:
            label = "80+"
        elif score >= 70:
            label = "70-80"
        elif score >= 60:
            label = "60-70"
        else:
            label = "<60"
        buckets[label].append(r)
    return _stats_from_buckets(buckets)


def _group_key_stats(records: List[dict], key: str) -> List[dict]:
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for r in records:
        val = str(r.get(key) or "unknown")
        buckets[val].append(r)
    return _stats_from_buckets(buckets)


def _stats_from_buckets(buckets: Dict[str, List[dict]]) -> List[dict]:
    rows: List[dict] = []
    win_th = WIN_THRESHOLD_PCT
    for key, parts in buckets.items():
        if not parts:
            continue
        wins = sum(1 for p in parts if p.get("is_win"))
        pos = sum(1 for p in parts if float(p.get("return_pct") or 0) > 0)
        rets = [float(p["return_pct"]) for p in parts if p.get("return_pct") is not None]
        rows.append({
            "key": key,
            "count": len(parts),
            "win_rate": round(wins / len(parts) * 100, 1),
            "positive_rate": round(pos / len(parts) * 100, 1),
            "avg_return": round(sum(rets) / len(rets), 2) if rets else 0.0,
            "win_threshold_pct": win_th,
        })
    rows.sort(key=lambda x: (-x["count"], -x["win_rate"]))
    return rows


def _interim_return_for_rec(rec: dict) -> Optional[float]:
    """计算单只 tracking 标的截止最新交易日的中间收益。"""
    pick_date = rec.get("pick_date", "")
    pick_price = float(rec.get("pick_price") or 0)
    code = str(rec.get("code", "")).zfill(6)
    if not pick_date or pick_price <= 0 or not code:
        return None
    cal = _trading_days_between(pick_date, _today())
    if len(cal) < INTERIM_MIN_HOLD_DAYS:
        return None
    try:
        hist = _normalize_hist_dates(get_stock_hist(code, days=40, patch_live=False))
    except Exception:
        return None
    if hist.empty or "close" not in hist.columns:
        return None
    window = hist[hist["date"] >= pick_date]
    if window.empty:
        return None
    close = pd.to_numeric(window["close"], errors="coerce").dropna()
    if close.empty:
        return None
    last_price = float(close.iloc[-1])
    return (last_price - pick_price) / pick_price * 100


def _enrich_interim_records(records: List[dict]) -> List[dict]:
    """为 tracking 记录附加中间收益字段。"""
    enriched: List[dict] = []
    win_th = WIN_THRESHOLD_PCT
    for rec in records:
        if rec.get("status") != "tracking":
            continue
        ret = _interim_return_for_rec(rec)
        if ret is None:
            continue
        copy = dict(rec)
        copy["return_pct"] = round(ret, 2)
        copy["is_win"] = ret >= win_th
        copy["is_positive"] = ret > 0
        enriched.append(copy)
    return enriched


def compute_interim_summary(state: Optional[dict] = None) -> dict:
    """基于 tracking 池中间收益统计因子表现（成熟样本不足时使用）。"""
    state = state or _load_state()
    tracking = [r for r in state.get("records", []) if r.get("status") == "tracking"]
    interim = _enrich_interim_records(tracking)
    win_th = float(state.get("win_threshold_pct", WIN_THRESHOLD_PCT))

    if len(interim) < INTERIM_MIN_SAMPLES:
        return {
            "interim_count": len(interim),
            "tracking_count": len(tracking),
            "matured_count": 0,
            "win_rate": 0.0,
            "positive_rate": 0.0,
            "avg_return": 0.0,
            "win_threshold_pct": win_th,
            "follow_trading_days": state.get("follow_trading_days", FOLLOW_TRADING_DAYS),
            "by_condition": [],
            "by_tag": [],
            "by_score_bucket": [],
            "by_ma60_trend": [],
            "factor_insights": [],
            "provisional": True,
        }

    wins = [r for r in interim if r.get("is_win")]
    positive = [r for r in interim if r.get("is_positive")]
    rets = [float(r["return_pct"]) for r in interim]

    by_condition = _group_factor_stats(interim, "conditions")
    by_tag = _group_tag_stats(interim)
    by_score = _group_score_stats(interim)
    by_ma60 = _group_key_stats(interim, "ma60_trend")
    insights = _build_factor_insights(by_condition, by_tag, by_score, by_ma60)
    if insights:
        insights.insert(
            0,
            f"【中间统计·{len(interim)}只】胜率(≥{win_th}%) "
            f"{round(len(wins) / len(interim) * 100, 1)}%，"
            f"均收益 {round(sum(rets) / len(rets), 2):+.2f}%",
        )

    return {
        "interim_count": len(interim),
        "tracking_count": len(tracking),
        "matured_count": 0,
        "win_rate": round(len(wins) / len(interim) * 100, 1),
        "positive_rate": round(len(positive) / len(interim) * 100, 1),
        "avg_return": round(sum(rets) / len(rets), 2),
        "win_threshold_pct": win_th,
        "follow_trading_days": state.get("follow_trading_days", FOLLOW_TRADING_DAYS),
        "by_condition": by_condition,
        "by_tag": by_tag,
        "by_score_bucket": by_score,
        "by_ma60_trend": by_ma60,
        "factor_insights": insights[:12],
        "provisional": True,
    }


def derive_interim_factor_tuning(summary: dict) -> Dict[str, Any]:
    """从中间统计推导 provisional 因子加减分（样本门槛低于成熟统计）。"""
    changes: Dict[str, Any] = {
        "midterm_condition_bonus": {},
        "midterm_condition_penalty": {},
        "midterm_tag_bonus": {},
        "midterm_tag_penalty": {},
        "midterm_min_score": None,
        "notes": [],
    }
    if summary.get("interim_count", 0) < INTERIM_MIN_SAMPLES:
        return changes

    from quantpy.midterm_portfolio_advisor import _CONDITION_LABELS

    min_n = INTERIM_MIN_SAMPLES
    overall_wr = float(summary.get("win_rate", 0))
    for row in summary.get("by_condition", []):
        if row["count"] < min_n:
            continue
        key = row["key"]
        label = _CONDITION_LABELS.get(key, key)
        # 底背离基础条件全员具备，不做降权
        if key in ("diff_div", "obv_div", "price_new_low", "diff_below_zero", "vol_shrink"):
            continue
        if row["win_rate"] >= max(22, overall_wr + 5) and row["avg_return"] > -0.3:
            changes["midterm_condition_bonus"][key] = 6
            changes["notes"].append(f"中间强化 {label}（胜率{row['win_rate']}%）")
        elif row["win_rate"] < max(12, overall_wr - 5) and row["count"] >= min_n:
            changes["midterm_condition_penalty"][key] = 5
            changes["notes"].append(f"中间降权 {label}（胜率{row['win_rate']}%）")

    tag_bonus_map = {
        "RSI底背离": ("rsi_div", 6),
        "绿柱缩短": ("stop_confirm", 5),
        "MACD金叉": ("entry_confirm", 4),
        "MA60走平/向上": ("ma60_hold", 5),
        "MA60向上": ("ma60_hold", 6),
        "贴近MA60": ("near_ma60", 4),
        "60分底背离": ("stop_confirm", 3),
    }
    tag_penalty_map = {
        "等金叉确认": 8,
        "MA60向下": 10,
        "偏远离MA60": 6,
        "当日偏弱": 5,
        "弱止跌确认": 6,
    }

    for row in summary.get("by_tag", []):
        if row["count"] < min_n:
            continue
        tag = row["key"]
        rel_strong = row["win_rate"] >= max(20, overall_wr + 4)
        rel_weak = row["win_rate"] < max(12, overall_wr - 4)
        if rel_strong:
            changes["midterm_tag_bonus"][tag] = 5
            if tag in tag_bonus_map:
                cond, bonus = tag_bonus_map[tag]
                changes["midterm_condition_bonus"][cond] = max(
                    changes["midterm_condition_bonus"].get(cond, 0), bonus,
                )
            if row["avg_return"] > 0:
                changes["notes"].append(f"中间强化标签 {tag}（胜率{row['win_rate']}%）")
        elif rel_weak:
            changes["midterm_tag_penalty"][tag] = tag_penalty_map.get(tag, 6)
            changes["notes"].append(f"中间降权标签 {tag}（胜率{row['win_rate']}%）")

    for tag, penalty in tag_penalty_map.items():
        for row in summary.get("by_tag", []):
            if row["key"] == tag and row["count"] >= min_n and row["win_rate"] < max(15, overall_wr - 3):
                changes["midterm_tag_penalty"][tag] = max(
                    changes["midterm_tag_penalty"].get(tag, 0), penalty,
                )

    avg_ret = float(summary.get("avg_return", 0))
    if overall_wr < 20 or avg_ret < -0.5:
        changes["midterm_min_score"] = 68
        changes["notes"].append(
            f"中间胜率 {overall_wr}% / 均收益 {avg_ret:+.2f}%，抬高评分门槛"
        )
    elif overall_wr >= 35 and avg_ret > 1:
        changes["midterm_min_score"] = 66

    for row in summary.get("by_score_bucket", []):
        if row["key"] in ("<60", "60-70") and row["count"] >= min_n and row["win_rate"] < 15:
            changes["midterm_min_score"] = max(changes.get("midterm_min_score") or 65, 68)

    return changes


def _build_factor_insights(
    by_condition: List[dict],
    by_tag: List[dict],
    by_score: List[dict],
    by_ma60: List[dict],
) -> List[str]:
    from quantpy.midterm_portfolio_advisor import _CONDITION_LABELS

    insights: List[str] = []
    for row in by_condition:
        if row["count"] < MIN_SAMPLES_FOR_FACTOR:
            continue
        label = _CONDITION_LABELS.get(row["key"], row["key"])
        if row["win_rate"] >= 55 and row["avg_return"] > 2:
            insights.append(
                f"因子「{label}」胜率 {row['win_rate']}%（{row['count']} 笔），"
                f"均收益 {row['avg_return']:+.2f}%，建议强化"
            )
        elif row["win_rate"] < 40 and row["count"] >= 4:
            insights.append(
                f"因子「{label}」胜率 {row['win_rate']}% 偏低（{row['count']} 笔），"
                f"建议降权或提高门槛"
            )

    for row in by_tag[:6]:
        if row["count"] < MIN_SAMPLES_FOR_FACTOR:
            continue
        if row["win_rate"] >= 58:
            insights.append(
                f"标签「{row['key']}」跟进胜率 {row['win_rate']}%（{row['count']} 笔）"
            )

    for row in by_score:
        if row["count"] >= 2:
            insights.append(
                f"评分 {row['key']}：{row['count']} 笔，胜率 {row['win_rate']}%，"
                f"均收益 {row['avg_return']:+.2f}%"
            )

    for row in by_ma60:
        if row["count"] < MIN_SAMPLES_FOR_FACTOR:
            continue
        if row["key"] in ("flat", "up") and row["win_rate"] >= 50:
            insights.append(f"MA60{row['key']} 组胜率 {row['win_rate']}%（{row['count']} 笔）")
        elif row["key"] == "down" and row["win_rate"] < 45:
            insights.append(f"MA60向下组胜率仅 {row['win_rate']}%，宜轻仓或过滤")

    return insights[:12]


def derive_factor_tuning(
    summary: dict,
    interim_summary: Optional[dict] = None,
) -> Dict[str, Any]:
    """从跟进统计推导选股因子加减分；成熟样本不足时使用中间统计。"""
    if summary.get("matured_count", 0) >= MIN_SAMPLES_FOR_FACTOR:
        return _derive_matured_factor_tuning(summary)
    if interim_summary and interim_summary.get("interim_count", 0) >= INTERIM_MIN_SAMPLES:
        return derive_interim_factor_tuning(interim_summary)
    return {
        "midterm_condition_bonus": {},
        "midterm_condition_penalty": {},
        "midterm_tag_bonus": {},
        "midterm_tag_penalty": {},
        "midterm_min_score": None,
        "notes": [],
    }


def _derive_matured_factor_tuning(summary: dict) -> Dict[str, Any]:
    """从满期成熟样本推导因子加减分。"""
    from quantpy.midterm_portfolio_advisor import _CONDITION_LABELS

    changes: Dict[str, Any] = {
        "midterm_condition_bonus": {},
        "midterm_tag_bonus": {},
        "midterm_min_score": None,
        "notes": [],
    }

    for row in summary.get("by_condition", []):
        if row["count"] < MIN_SAMPLES_FOR_FACTOR:
            continue
        key = row["key"]
        label = _CONDITION_LABELS.get(key, key)
        if row["win_rate"] >= 58 and row["avg_return"] > 2:
            changes["midterm_condition_bonus"][key] = 8
            changes["notes"].append(f"跟进强化因子 {label}（胜率{row['win_rate']}%）")
        elif row["win_rate"] >= 52:
            changes["midterm_condition_bonus"][key] = 4
        elif row["win_rate"] < 38 and row["count"] >= 4:
            changes["midterm_condition_bonus"][key] = -5
            changes["notes"].append(f"跟进降权因子 {label}（胜率{row['win_rate']}%）")

    for row in summary.get("by_tag", []):
        if row["count"] < MIN_SAMPLES_FOR_FACTOR:
            continue
        if row["win_rate"] >= 58:
            changes["midterm_tag_bonus"][row["key"]] = 5
        elif row["win_rate"] < 35 and row["count"] >= 4:
            changes["midterm_tag_bonus"][row["key"]] = -4

    overall_wr = float(summary.get("win_rate", 0))
    if overall_wr < 40 and summary.get("matured_count", 0) >= 8:
        changes["midterm_min_score"] = 62
        changes["notes"].append(f"中线跟进胜率 {overall_wr}% 偏低，抬高评分门槛")
    elif overall_wr >= 55:
        changes["midterm_min_score"] = max(55, 58 - 2)

    for row in summary.get("by_score_bucket", []):
        if row["key"] == "<60" and row["count"] >= 2 and row["win_rate"] < 40:
            changes["midterm_min_score"] = max(changes.get("midterm_min_score") or 55, 62)

    return changes


def build_learning_suggestions(summary: dict) -> List[str]:
    """生成可展示的学习建议。"""
    interim = summary.get("interim") or {}
    if summary.get("matured_count", 0) == 0:
        if interim.get("interim_count", 0) >= INTERIM_MIN_SAMPLES:
            lines = [
                f"【中线跟进·中间】{interim['interim_count']} 只满 {INTERIM_MIN_HOLD_DAYS}+ 交易日，"
                f"胜率(≥{interim.get('win_threshold_pct', 3)}%) {interim['win_rate']}%，"
                f"均收益 {interim.get('avg_return', 0):+.2f}%",
            ]
            lines.extend(interim.get("factor_insights", [])[:5])
            tuning = derive_interim_factor_tuning(interim)
            if tuning.get("midterm_condition_bonus") or tuning.get("midterm_tag_penalty"):
                parts = []
                for k, v in tuning.get("midterm_condition_bonus", {}).items():
                    parts.append(f"{k}+{v}")
                for k, v in tuning.get("midterm_tag_penalty", {}).items():
                    parts.append(f"{k}-{v}")
                if parts:
                    lines.append(f"因子调优（中间）：{', '.join(parts[:8])}")
            return lines
        tracking = summary.get("tracking_count", 0)
        return [
            f"中线跟进池：{tracking} 只跟踪中，满 {summary.get('follow_trading_days', FOLLOW_TRADING_DAYS)} "
            f"交易日后统计胜率并优化因子"
        ]

    lines = [
        f"【中线跟进·{summary.get('follow_trading_days', FOLLOW_TRADING_DAYS)}日】"
        f"成熟 {summary['matured_count']} 只，"
        f"胜率(≥{summary.get('win_threshold_pct', 3)}%) {summary['win_rate']}%，"
        f"正收益率 {summary.get('positive_rate', 0)}%，"
        f"均收益 {summary.get('avg_return', 0):+.2f}%"
        f"（峰值均 {summary.get('avg_max_return', 0):+.2f}%）",
    ]
    lines.extend(summary.get("factor_insights", [])[:6])
    tuning = derive_factor_tuning(summary, interim)
    if tuning.get("midterm_condition_bonus"):
        parts = [
            f"{k}+{v}" if v > 0 else f"{k}{v}"
            for k, v in tuning["midterm_condition_bonus"].items()
        ]
        lines.append(f"因子调优：{', '.join(parts[:8])}")
    return lines


def _cached_interim_summary(state: dict, *, refresh: bool = False) -> dict:
    """读取或刷新中间统计缓存（避免每次扫描重复拉行情）。"""
    today = _today()
    cached = state.get("interim_summary") or {}
    if not refresh and state.get("interim_computed_at") == today:
        return cached
    try:
        interim = compute_interim_summary(state)
        state["interim_summary"] = interim
        state["interim_computed_at"] = today
        _save_state(state)
        return interim
    except Exception:
        return cached


def load_tracker_summary(*, evaluate: bool = True) -> dict:
    """加载跟进摘要；默认先尝试评估到期标的。评估失败不抛错。"""
    if evaluate:
        try:
            evaluate_matured_picks(show_progress=False)
        except Exception:
            pass
    try:
        state = _load_state()
        summary = state.get("summary") or compute_tracker_summary(state)
        interim = _cached_interim_summary(state, refresh=evaluate)
        summary = dict(summary)
        summary["interim"] = interim
        records = list(state.get("records") or [])
        # 按选股日倒序，同日内按排名升序
        records.sort(
            key=lambda r: (
                str(r.get("pick_date") or ""),
                -int(r.get("rank") or 999),
            ),
            reverse=True,
        )
        # reverse=True 后同日 rank 为倒序，再按日分组重排 rank 正序
        ordered: list = []
        cur_date = None
        bucket: list = []
        for r in records:
            d = str(r.get("pick_date") or "")
            if cur_date is None:
                cur_date = d
            if d != cur_date:
                ordered.extend(sorted(bucket, key=lambda x: int(x.get("rank") or 999)))
                bucket = [r]
                cur_date = d
            else:
                bucket.append(r)
        if bucket:
            ordered.extend(sorted(bucket, key=lambda x: int(x.get("rank") or 999)))

        tracking = [r for r in ordered if r.get("status") == "tracking"]
        matured = [r for r in ordered if r.get("status") == "matured"]
        return {
            "summary": summary,
            "records": ordered,
            "tracking": tracking,
            "matured_recent": matured,
            "suggestions": build_learning_suggestions(summary),
            "factor_tuning": derive_factor_tuning(summary, interim),
            "interim_summary": interim,
            "last_record_date": state.get("last_record_date", ""),
            "last_evaluated": state.get("last_evaluated", ""),
            "total_records": len(ordered),
        }
    except Exception:
        return {
            "summary": compute_tracker_summary(_default_state()),
            "records": [],
            "tracking": [],
            "matured_recent": [],
            "suggestions": [],
            "factor_tuning": {},
            "last_record_date": "",
            "last_evaluated": "",
            "total_records": 0,
        }


def run_midterm_tracker_cycle(
    recommendations: Optional[List[dict]] = None,
    *,
    show_progress: bool = False,
) -> dict:
    """完整周期：记录 TOP10 → 评估到期 → 返回摘要。"""
    if recommendations:
        record_midterm_top_picks(recommendations, show_progress=show_progress)
    eval_result = evaluate_matured_picks(show_progress=show_progress)
    payload = load_tracker_summary(evaluate=False)
    payload["eval"] = eval_result
    return payload
