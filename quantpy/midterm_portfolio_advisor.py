"""
实盘中线顾问
- 个股复盘：趋势、均线、RSI、支撑阻力
- 持仓优化：仓位配比、加减仓建议
- 个股推荐：中线趋势 + 适度动量筛选
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from quantpy.json_util import df_to_records_safe, json_safe_float, sanitize_for_json
from quantpy.paths import MIDTERM_OUTPUT_DIR
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.selection_tuning import SelectionTuning, build_selection_tuning, format_tuning_summary
from quantpy.stock_data import (
    ensure_industry_map,
    exclude_bse_from_df,
    get_fundamental_map,
    get_market_spot,
    get_stock_hist,
    get_stock_code_column,
    get_stock_name_column,
    is_bse_code,
)

ULTRA_SHORT_STRATEGIES = frozenset({"超短", "涨停"})


def _classify_bucket(strategy: str) -> str:
    return "ultra_short" if str(strategy) in ULTRA_SHORT_STRATEGIES else "midterm"

OUTPUT_DIR = MIDTERM_OUTPUT_DIR
MIDTERM_STRATEGIES = {"中线", "趋势", "手动", "价值", "波段", "ETF", "三倍量"}

PERFORMANCE_FILTER_OPTIONS = {
    "profit_growth": "净利正增长",
    "high_growth": "净利增≥30%",
    "low_pe": "低市盈率(0-30)",
    "value_growth": "低PE+正增长",
}

# 中线选股：趋势回调 + 底背离（避开自由落体）
DIVERGENCE_N_DAILY = 25
DIVERGENCE_N_60M = 15
MIN_DAILY_AMOUNT_WAN = 5000  # 5000 万元（行情 amount 列通常为万元）
MIDTERM_PREFILTER_DEFAULT = 500
MIDTERM_PREFILTER_MAX = 1200
MIDTERM_SCAN_WORKERS = 8
# 趋势风控：拒绝空头自由落体
MIDTERM_MAX_RET_20D = -12.0   # 20 日跌超 12% 不出
MIDTERM_MAX_RET_60D = -22.0   # 60 日跌超 22% 不出
MIDTERM_MIN_PRICE_TO_MA60 = 0.90  # 价相对 MA60 过低视为深套

# 中线选股条件展示
MIDTERM_SELECT_CONDITIONS = [
    {"id": "cap_range", "label": "市值150-1000亿", "category": "基本面"},
    {"id": "price_cap", "label": "股价<100元", "category": "基本面"},
    {"id": "liquidity", "label": "成交额≥5000万", "category": "基本面"},
    {"id": "ma60_hold", "label": "MA60走平/向上(软筛)", "category": "趋势"},
    {"id": "not_freefall", "label": "拒绝20/60日深跌", "category": "趋势"},
    {"id": "near_ma60", "label": "靠近MA60支撑带", "category": "趋势"},
    {"id": "price_new_low", "label": "股价创阶段新低", "category": "技术面"},
    {"id": "diff_div", "label": "DIFF底背离", "category": "技术面"},
    {"id": "obv_div", "label": "OBV资金底背离", "category": "技术面"},
    {"id": "diff_below_zero", "label": "DIFF零轴下", "category": "技术面"},
    {"id": "vol_shrink", "label": "缩量地量", "category": "技术面"},
    {"id": "stop_confirm", "label": "止跌确认(金叉/绿柱缩/RSI背离)", "category": "技术面"},
    {"id": "entry_confirm", "label": "MACD金叉确认", "category": "技术面"},
    {"id": "rsi_div", "label": "RSI底背离", "category": "技术面"},
]

_CONDITION_LABELS = {c["id"]: c["label"] for c in MIDTERM_SELECT_CONDITIONS}
MIDTERM_MIN_SCORE = 65


def get_midterm_select_conditions() -> List[dict]:
    return list(MIDTERM_SELECT_CONDITIONS)


def _amount_to_wan(value) -> float:
    """成交额统一为万元（腾讯/新浪行情 amount 多为万元）。"""
    if value is None or value == "":
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v >= 1e7:
        return v / 10000.0
    return v


def _passes_liquidity(amount_wan: Optional[float]) -> bool:
    if amount_wan is None or amount_wan <= 0:
        return True
    return amount_wan >= MIN_DAILY_AMOUNT_WAN


def _hist_pct_series(hist: pd.DataFrame) -> pd.Series:
    if "pct_chg" in hist.columns:
        s = pd.to_numeric(hist["pct_chg"], errors="coerce")
        if s.notna().any():
            return s.fillna(0)
    close = pd.to_numeric(hist["close"], errors="coerce")
    return close.pct_change() * 100


def _evaluate_midterm_technicals(
    hist: pd.DataFrame,
    spot_pct: float,
    turnover: float,
    min_daily_gain_pct: float = 2.0,
    tuning: Optional[SelectionTuning] = None,
    *,
    name: str = "",
    daily_amount: Optional[float] = None,
    code: str = "",
    check_60m: bool = True,
) -> Optional[dict]:
    """
    中线选股：趋势回调中的 MACD+OBV 底背离（日线 N=25）。

    硬筛：
    - 底背离五要素（阶段新低 + DIFF/OBV 背离 + DIFF<0 + 缩量）
    - 拒绝 20/60 日深跌与远离 MA60 的深套
    - 强止跌确认（金叉 / 绿柱缩短 / RSI 底背离）；仅收复短均不够
    - MA60 向下允许（跟进成熟样本更优），但必须强止跌确认
    """
    del min_daily_gain_pct, turnover

    if _is_st_or_delist_name(name):
        return None

    pick = _evaluate_tdx_bottom_divergence(
        hist, n=DIVERGENCE_N_DAILY, code=code, check_60m=check_60m,
    )
    if pick is None or not pick.get("signal"):
        return None

    if daily_amount is not None and daily_amount > 0:
        amount_wan = _amount_to_wan(daily_amount)
        if not _passes_liquidity(amount_wan):
            return None
    if spot_pct <= -9.5:
        return None

    close = pd.to_numeric(hist["close"], errors="coerce")
    price = float(close.iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1]) if len(close) >= 5 else price
    ma10 = float(close.rolling(10).mean().iloc[-1]) if len(close) >= 10 else price
    ma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else price
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else price
    rsi = pick.get("rsi", _rsi(close))
    ret_20d = _safe_pct(price, float(close.iloc[-21])) if len(close) >= 21 else 0.0
    ret_60d = _safe_pct(price, float(close.iloc[-61])) if len(close) >= 61 else 0.0

    ma60_trend = pick.get("ma60_trend", _ma60_trend_label(close))
    # ---- 深跌硬筛：拒绝自由落体（保留）；MA60 方向改由强止跌 + 评分控制 ----
    if ret_20d < MIDTERM_MAX_RET_20D:
        return None
    if ret_60d < MIDTERM_MAX_RET_60D:
        return None
    if ma60 > 0 and price < ma60 * MIDTERM_MIN_PRICE_TO_MA60:
        return None

    # MA20 中期也在加速下杀时放弃（即便 MA60 仍名义走平）
    if len(close) >= 25:
        ma20_prev = float(close.rolling(20).mean().iloc[-6])
        if ma20_prev > 0 and (ma20 - ma20_prev) / ma20_prev * 100 < -2.5 and ret_20d < -8:
            return None

    has_golden = bool(pick.get("macd_golden_cross"))
    has_bar_shrink = bool(pick.get("macd_bar_shrink"))
    has_rsi_div = bool(pick.get("rsi_divergence"))
    reclaim_ma = price >= ma5 * 0.998 or price >= ma10 * 0.99
    strong_confirm = has_golden or has_bar_shrink or has_rsi_div
    # 跟进成熟样本：MA60 向下组胜率更高；走平组偏弱。
    # 一律要求强止跌确认（金叉/绿柱缩/RSI），仅收复短均不再过筛。
    if not strong_confirm:
        return None

    tags: List[str] = list(pick.get("tags", []))
    conditions: List[str] = [
        "cap_range", "price_cap", "liquidity",
        "not_freefall", "near_ma60",
        "price_new_low", "diff_div", "obv_div", "diff_below_zero", "vol_shrink",
        "stop_confirm",
    ]
    if ma60_trend in ("up", "flat"):
        conditions.append("ma60_hold")
    score = 55

    # 评分权重按跟进成熟结果校准（向下 > 向上 ≈ 走平）
    if ma60_trend == "down":
        score += 14
        tags.append("MA60向下")
        trend = "下跌趋势底背离反转"
        hold_style = "MA60仍向下，仅作轻仓反转博弈，须强止跌+严格止损"
    elif ma60_trend == "up":
        score += 12
        tags.append("MA60向上")
        trend = "趋势回调底背离"
        hold_style = "均线多头/走强中的回调底背离，适合中线"
    elif ma60_trend == "flat":
        score += 6
        tags.append("MA60走平")
        trend = "震荡筑底背离"
        hold_style = "MA60走平筑底偏弱，需更强确认、控制仓位"
    else:
        score += 2
        tags.append("MA60未知")
        trend = "弱趋势筑底"
        hold_style = "趋势不明，需更强止跌信号"

    # 靠近 MA60 支撑加分
    if ma60 > 0:
        dist_ma60 = (price / ma60 - 1) * 100
        if -6 <= dist_ma60 <= 3:
            score += 10
            tags.append("贴近MA60")
        elif dist_ma60 > 8:
            score -= 8
            tags.append("偏远离MA60")

    if has_rsi_div:
        score += 18
        tags.append("RSI底背离")
        conditions.append("rsi_div")
    elif 28 <= float(rsi) <= 42:
        score += 4
        tags.append("RSI回调区")

    if has_golden:
        score += 14
        tags.append("MACD金叉")
        conditions.append("entry_confirm")
        entry_hint = "DIFF上穿DEA，可考虑分批介入"
        if has_rsi_div or has_bar_shrink:
            score += 4
    elif has_bar_shrink:
        score += 16
        tags.append("绿柱缩短")
        entry_hint = "绿柱缩短止跌，等金叉或放量再加仓"
    else:
        entry_hint = "已具备强止跌信号，仍建议观察量能"

    if pick.get("confirm_60m"):
        score += 8
        tags.append("60分底背离")

    # 今日仍大跌则减分（抄底当日追跌）
    if spot_pct <= -5:
        score -= 10
        tags.append("当日偏弱")
    elif -3 <= spot_pct <= 2:
        score += 4

    if ret_20d >= -5:
        score += 6
    elif ret_20d <= -10:
        score -= 8

    if tuning:
        for cond in conditions:
            score += tuning.midterm_condition_bonus.get(cond, 0)
            score -= tuning.midterm_condition_penalty.get(cond, 0)
        for tag_key, bonus in tuning.midterm_tag_bonus.items():
            if any(tag_key in t or t == tag_key for t in tags):
                score += bonus
        for tag_key, penalty in tuning.midterm_tag_penalty.items():
            if any(tag_key in t or t == tag_key for t in tags):
                score -= penalty
        if tuning.midterm_penalize_ret_20d_below is not None and ret_20d < tuning.midterm_penalize_ret_20d_below:
            score -= 10

    if tuning and tuning.midterm_ma20_chase_penalty > 0 and ma20 > 0:
        if price > ma20 * tuning.midterm_ma20_chase_ratio:
            score -= tuning.midterm_ma20_chase_penalty

    min_score = tuning.midterm_min_score if tuning else MIDTERM_MIN_SCORE
    # 尊重 AI/跟进调优门槛（build_selection_tuning 已限制在 58~72）
    if score < min_score:
        return None

    return {
        "score": score,
        "tags": tags,
        "conditions": conditions,
        "price": price,
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "rsi": round(float(rsi), 1),
        "ret_20d": round(ret_20d, 2),
        "ret_60d": round(ret_60d, 2),
        "trend": trend,
        "hold_style": hold_style,
        "entry_hint": entry_hint,
        "ma60_trend": ma60_trend,
        "bottom_divergence": True,
        "bottom_divergence_detail": pick,
        "stop_confirm": True,
    }


def _num(value) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _passes_performance_filter(item: dict, performance: str) -> bool:
    pe_raw = item.get("pe")
    yoy_raw = item.get("profit_yoy")
    if performance in ("low_pe", "value_growth"):
        if pe_raw is None or (isinstance(pe_raw, float) and pd.isna(pe_raw)):
            return False
    if performance in ("profit_growth", "high_growth", "value_growth"):
        if yoy_raw is None or (isinstance(yoy_raw, float) and pd.isna(yoy_raw)):
            return False
    pe = _num(pe_raw)
    yoy = _num(yoy_raw)
    if performance == "profit_growth":
        return yoy > 0
    if performance == "high_growth":
        return yoy >= 30
    if performance == "low_pe":
        return 0 < pe <= 30
    if performance == "value_growth":
        return yoy > 0 and 0 < pe <= 40
    return True


def _apply_recommendation_filters(
    items: List[dict],
    industry: Optional[str] = None,
    performance: Optional[str] = None,
) -> Tuple[List[dict], dict]:
    """行业/业绩筛选，返回 (结果, 统计)。"""
    stats: dict = {
        "input_count": len(items),
        "industry": industry or "",
        "performance": performance or "",
        "after_industry": len(items),
        "after_performance": len(items),
        "industry_no_data": 0,
        "performance_no_pe": 0,
        "performance_no_yoy": 0,
        "performance_not_match": 0,
        "output_count": len(items),
        "top_industries": [],
    }
    out = items
    if industry:
        no_ind = sum(1 for r in out if not (r.get("industry") or "").strip())
        stats["industry_no_data"] = no_ind
        out = [r for r in out if (r.get("industry") or "") == industry]
        stats["after_industry"] = len(out)
        _progress(
            f"  [筛选] 行业「{industry}」: {stats['input_count']} → {len(out)} 只"
            + (f"（{no_ind} 只无行业数据）" if no_ind else ""),
            True,
        )
    if performance:
        label = PERFORMANCE_FILTER_OPTIONS.get(performance, performance)
        passed: List[dict] = []
        no_pe = no_yoy = not_match = 0
        for r in out:
            pe_raw = r.get("pe")
            yoy_raw = r.get("profit_yoy")
            need_pe = performance in ("low_pe", "value_growth")
            need_yoy = performance in ("profit_growth", "high_growth", "value_growth")
            if need_pe and (pe_raw is None or (isinstance(pe_raw, float) and pd.isna(pe_raw))):
                no_pe += 1
                continue
            if need_yoy and (yoy_raw is None or (isinstance(yoy_raw, float) and pd.isna(yoy_raw))):
                no_yoy += 1
                continue
            if _passes_performance_filter(r, performance):
                passed.append(r)
            else:
                not_match += 1
        stats["performance_no_pe"] = no_pe
        stats["performance_no_yoy"] = no_yoy
        stats["performance_not_match"] = not_match
        stats["after_performance"] = len(passed)
        out = passed
        parts = [f"{stats.get('after_industry', stats['input_count'])} → {len(out)} 只"]
        if no_pe:
            parts.append(f"缺PE {no_pe}")
        if no_yoy:
            parts.append(f"缺净利同比 {no_yoy}")
        if not_match:
            parts.append(f"未达标 {not_match}")
        _progress(f"  [筛选] 业绩「{label}」: " + "，".join(parts), True)

    if not industry and items:
        ctr = Counter((r.get("industry") or "—") for r in items)
        stats["top_industries"] = [f"{k}({v})" for k, v in ctr.most_common(8)]

    stats["output_count"] = len(out)
    return out, stats


def _progress(msg: str, show: bool = True) -> None:
    if show:
        print(msg, flush=True)


def _rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 1:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    last_loss = loss.iloc[-1]
    if pd.isna(last_loss) or last_loss == 0:
        return 100.0 if gain.iloc[-1] > 0 else 50.0
    rs = gain.iloc[-1] / last_loss
    return float(100 - 100 / (1 + rs))


def _macd_series(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """返回 MACD 的 DIF、DEA、BAR 序列。"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    bar = (dif - dea) * 2
    return dif, dea, bar


def _pivot_low_indices(series: pd.Series, order: int = 3) -> List[int]:
    """局部低点索引（左右各 order 根 K 线）。"""
    vals = pd.to_numeric(series, errors="coerce")
    n = len(vals)
    lows: List[int] = []
    for i in range(order, n - order):
        window = vals.iloc[i - order : i + order + 1]
        if window.isna().any():
            continue
        center = float(vals.iloc[i])
        left_min = float(vals.iloc[i - order : i].min())
        right_min = float(vals.iloc[i + 1 : i + order + 1].min())
        if center <= left_min and center <= right_min:
            lows.append(i)
    return lows


def _detect_bottom_divergence(
    close: pd.Series,
    lookback: int = 60,
    min_sep: int = 5,
    max_sep: int = 40,
) -> Optional[dict]:
    """
    MACD 底背离：价格创新低，DIF 低点抬高（通常在零轴下方）。
    返回最近一组有效背离，含距当前 K 线的 bars_ago。
    """
    close = pd.to_numeric(close, errors="coerce")
    if len(close) < lookback + 26:
        return None

    segment = close.iloc[-lookback:].reset_index(drop=True)
    dif, _, _ = _macd_series(close)
    dif_seg = dif.iloc[-lookback:].reset_index(drop=True)

    price_lows = _pivot_low_indices(segment, order=3)
    if len(price_lows) < 2:
        return None

    for i in range(len(price_lows) - 1, 0, -1):
        idx2 = price_lows[i]
        for j in range(i - 1, -1, -1):
            idx1 = price_lows[j]
            sep = idx2 - idx1
            if sep < min_sep:
                continue
            if sep > max_sep:
                break
            p1 = float(segment.iloc[idx1])
            p2 = float(segment.iloc[idx2])
            d1 = float(dif_seg.iloc[idx1])
            d2 = float(dif_seg.iloc[idx2])
            if p1 <= 0 or p2 <= 0:
                continue
            if p2 >= p1 * 0.998:
                continue
            if d2 <= d1 * 1.02:
                continue
            if d1 > 0 and d2 > 0:
                continue
            bars_ago = lookback - 1 - idx2
            return {
                "bars_ago": bars_ago,
                "price_low1": round(p1, 2),
                "price_low2": round(p2, 2),
                "dif_low1": round(d1, 4),
                "dif_low2": round(d2, 4),
            }
    return None


def _is_st_or_delist_name(name: str) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "退" in str(name or "")


def _calc_obv(hist: pd.DataFrame) -> pd.Series:
    close = pd.to_numeric(hist["close"], errors="coerce")
    if "open" in hist.columns:
        open_px = pd.to_numeric(hist["open"], errors="coerce")
    else:
        open_px = close
    vol = pd.to_numeric(hist.get("volume", 0), errors="coerce").fillna(0)
    signed = vol.where(close > open_px, vol.where(close < open_px, 0) * -1)
    return signed.cumsum()


def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    """通达信 SMA 风格 RSI 序列。"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def _ma60_trend_label(close: pd.Series) -> str:
    if len(close) < 65:
        return "unknown"
    ma60 = close.rolling(60).mean()
    now = float(ma60.iloc[-1])
    prev5 = float(ma60.iloc[-6])
    prev15 = float(ma60.iloc[-16]) if len(ma60) >= 16 else prev5
    if prev5 <= 0:
        return "unknown"
    slope5 = (now - prev5) / prev5 * 100
    slope15 = (now - prev15) / prev15 * 100 if prev15 > 0 else slope5
    # 5日急跌或 15 日持续走弱 → down
    if slope5 < -0.35 or slope15 < -0.8:
        return "down"
    if slope5 > 0.35 and slope15 > 0:
        return "up"
    return "flat"


def _has_continuous_limit_down(hist: pd.DataFrame, days: int = 3) -> bool:
    pct = _hist_pct_series(hist)
    if len(pct) < days:
        return False
    recent = pd.to_numeric(pct.iloc[-days:], errors="coerce")
    return bool((recent <= -9.5).all())


def _tdx_divergence_on_hist(hist: pd.DataFrame, n: int) -> Optional[dict]:
    """在 K 线序列上判定通达信底背离五要素（最后一根）。"""
    if hist is None or len(hist) < n + 2:
        return None
    hist = hist.sort_values("date").reset_index(drop=True) if "date" in hist.columns else hist.reset_index(drop=True)
    low = pd.to_numeric(hist["low"], errors="coerce")
    close = pd.to_numeric(hist["close"], errors="coerce")
    vol = pd.to_numeric(hist.get("volume", 0), errors="coerce").fillna(0)
    if low.isna().iloc[-1] or close.isna().iloc[-1]:
        return None

    dif, dea, bar = _macd_series(close)
    obv = _calc_obv(hist)
    rsi_s = _rsi_series(close)

    ll = low.rolling(n).min()
    ll_diff = dif.rolling(n).min()
    ll_obv = obv.rolling(n).min()
    ll_rsi = rsi_s.rolling(n).min()

    ll_prev = ll.shift(1)
    ll_diff_prev = ll_diff.shift(1)
    ll_obv_prev = ll_obv.shift(1)
    ll_rsi_prev = ll_rsi.shift(1)

    i = -1
    price_new_low = float(low.iloc[i]) <= float(ll_prev.iloc[i])
    diff_div = float(dif.iloc[i]) > float(ll_diff_prev.iloc[i])
    obv_div = float(obv.iloc[i]) > float(ll_obv_prev.iloc[i])
    below_zero = float(dif.iloc[i]) < 0
    vol_ma5 = float(vol.rolling(5).mean().iloc[i])
    vol_ma10 = float(vol.rolling(10).mean().iloc[i])
    vol_shrink = vol_ma10 > 0 and vol_ma5 < vol_ma10 * 0.85

    signal = price_new_low and diff_div and obv_div and below_zero and vol_shrink
    if not signal:
        return None

    rsi_val = float(rsi_s.iloc[i])
    rsi_div = rsi_val > float(ll_rsi_prev.iloc[i]) and rsi_val < 30
    golden = float(dif.iloc[i]) > float(dea.iloc[i]) and float(dif.iloc[i - 1]) <= float(dea.iloc[i - 1])
    bar_shrink = float(bar.iloc[i]) > float(bar.iloc[i - 1]) and float(bar.iloc[i]) < 0

    return {
        "signal": True,
        "price_new_low": price_new_low,
        "diff_div": diff_div,
        "obv_div": obv_div,
        "below_zero": below_zero,
        "vol_shrink": vol_shrink,
        "rsi": rsi_val,
        "rsi_divergence": rsi_div,
        "macd_golden_cross": golden,
        "macd_bar_shrink": bar_shrink,
        "dif": round(float(dif.iloc[i]), 4),
        "dea": round(float(dea.iloc[i]), 4),
        "ma60_trend": _ma60_trend_label(close),
    }


def _evaluate_tdx_bottom_divergence(
    hist: pd.DataFrame,
    n: int = DIVERGENCE_N_DAILY,
    code: str = "",
    check_60m: bool = True,
) -> Optional[dict]:
    if _has_continuous_limit_down(hist):
        return None
    daily = _tdx_divergence_on_hist(hist, n)
    if daily is None:
        return None

    tags = ["DIFF底背离", "OBV底背离", "阶段新低", "缩量地量"]
    confirm_60m = False
    if check_60m and code:
        try:
            hist60 = get_stock_hist(code, days=40, freq="60", patch_live=False)
            confirm_60m = _tdx_divergence_on_hist(hist60, DIVERGENCE_N_60M) is not None
        except Exception:
            confirm_60m = False

    daily["tags"] = tags
    daily["confirm_60m"] = confirm_60m
    return daily


def _safe_pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100


def _spot_price_series(df: pd.DataFrame) -> pd.Series:
    for col in ("price", "close", "最新价"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(index=df.index, dtype=float)


def _market_cap_to_yi(series: pd.Series) -> pd.Series:
    """总市值统一为亿元（兼容元 / 万元）。"""
    cap = pd.to_numeric(series, errors="coerce")
    median = cap.median()
    if pd.notna(median) and median >= 1e8:
        return cap / 1e8
    return cap / 1e4


class MidtermPortfolioAdvisor:
    """实盘中线：个股复盘、持仓优化、推荐。"""

    def __init__(
        self,
        max_single_weight: float = 30.0,
        target_position_count: Tuple[int, int] = (3, 6),
        max_recommend_market_cap: float = 1000.0,
        min_recommend_market_cap: float = 150.0,
        max_recommend_price: float = 100.0,
    ):
        self.max_single_weight = max_single_weight
        self.target_position_count = target_position_count
        self.max_recommend_market_cap = max_recommend_market_cap
        self.min_recommend_market_cap = min_recommend_market_cap
        self.max_recommend_price = max_recommend_price
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def analyze_stock(
        self,
        code: str,
        name: str = "",
        cost_price: float = 0,
        weight_pct: float = 0,
    ) -> dict:
        """单只股票中线技术面复盘。"""
        code = str(code).zfill(6)
        hist = get_stock_hist(code, days=130)
        if hist.empty or len(hist) < 30:
            return {
                "code": code,
                "name": name or code,
                "ok": False,
                "message": "K线数据不足",
            }

        hist = hist.sort_values("date").reset_index(drop=True)
        close = pd.to_numeric(hist["close"], errors="coerce")
        high = pd.to_numeric(hist["high"], errors="coerce")
        low = pd.to_numeric(hist["low"], errors="coerce")
        volume = pd.to_numeric(hist.get("volume", 0), errors="coerce")

        price = float(close.iloc[-1])
        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma10 = float(close.rolling(10).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        rsi = round(_rsi(close), 1)

        ret_20d = _safe_pct(price, float(close.iloc[-21])) if len(close) >= 21 else 0
        ret_60d = _safe_pct(price, float(close.iloc[-61])) if len(close) >= 61 else 0
        bottom_div = _detect_bottom_divergence(close)

        vol_avg20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else 0
        vol_ratio = float(volume.iloc[-1] / vol_avg20) if vol_avg20 > 0 else 1.0

        recent_low = float(low.iloc[-20:].min())
        recent_high = float(high.iloc[-20:].max())
        support = round(max(ma20, recent_low * 0.98), 2)
        resistance = round(min(recent_high, ma20 * 1.15) if ma20 > 0 else recent_high, 2)

        trend = "震荡"
        tags: List[str] = []
        score = 50

        if price >= ma20 >= ma60:
            trend = "多头"
            score += 18
            tags.append("均线多头")
        elif price < ma20 <= ma60:
            trend = "空头"
            score -= 15
            tags.append("均线空头")
        else:
            tags.append("均线纠缠")

        if price >= ma20:
            score += 8
        if ma20 >= ma60:
            score += 6

        if 45 <= rsi <= 65:
            score += 10
            tags.append("RSI健康")
        elif rsi >= 75:
            score -= 8
            tags.append("RSI超买")
        elif rsi <= 35:
            tags.append("RSI超卖")
            if price >= ma60 * 0.97:
                score += 5
                tags.append("支撑位附近")

        if 3 <= ret_20d <= 20:
            score += 8
            tags.append("20日适度强势")
        elif ret_20d > 25:
            score -= 6
            tags.append("20日涨幅偏大")
        elif ret_20d < -8:
            score -= 10
            tags.append("20日走弱")

        if 0.8 <= vol_ratio <= 2.0:
            score += 4

        if bottom_div:
            div_bonus = 12 if bottom_div["bars_ago"] <= 10 else 8
            score += div_bonus
            tags.append("MACD底背离")
            if trend != "多头" and price >= ma10 * 0.97:
                score += 4
                tags.append("底背离企稳")

        profit_pct = _safe_pct(price, cost_price) if cost_price > 0 else 0
        action = "持有观望"
        action_reasons: List[str] = []

        if trend == "多头" and profit_pct >= 15:
            action = "分批止盈"
            action_reasons.append("中线趋势仍在，但浮盈较大，建议分批落袋")
        elif trend == "多头" and profit_pct >= 5:
            action = "持有"
            action_reasons.append("趋势完好，沿 MA20 持有，跌破 MA20 再评估减仓")
        elif trend == "多头" and profit_pct < -5:
            action = "持有观察"
            action_reasons.append("趋势未坏但浮亏，观察能否在 MA20 附近企稳")
        elif trend == "空头" and profit_pct <= -8:
            action = "减仓/止损"
            action_reasons.append("趋势转弱且浮亏较深，中线逻辑破坏应考虑减仓")
        elif trend == "空头":
            action = "减仓观望"
            action_reasons.append("均线空头排列，不宜中线加仓")
        elif trend == "震荡":
            action = "区间操作"
            action_reasons.append(f"震荡市，关注支撑 {support} / 阻力 {resistance}")

        if bottom_div and bottom_div["bars_ago"] <= 15 and trend != "空头":
            if action in ("持有观望", "区间操作", "持有观察"):
                action = "关注反弹"
            action_reasons.append(
                f"MACD底背离（{bottom_div['bars_ago']}日前低点），价格低点抬高可关注企稳"
            )

        if weight_pct > self.max_single_weight:
            action_reasons.append(
                f"单票占比 {weight_pct:.1f}% 偏高，中线建议单票≤{self.max_single_weight:.0f}%"
            )

        stop_suggest = round(max(support * 0.97, ma60 * 0.95) if ma60 > 0 else support * 0.95, 2)

        return {
            "ok": True,
            "code": code,
            "name": name or code,
            "price": round(price, 2),
            "cost_price": round(cost_price, 2) if cost_price else 0,
            "profit_pct": round(profit_pct, 2),
            "weight_pct": round(weight_pct, 2),
            "trend": trend,
            "midterm_score": max(0, min(100, score)),
            "rsi": rsi,
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "ret_20d": round(ret_20d, 2),
            "ret_60d": round(ret_60d, 2),
            "vol_ratio": round(vol_ratio, 2),
            "support": support,
            "resistance": resistance,
            "stop_suggest": stop_suggest,
            "action": action,
            "action_reasons": action_reasons,
            "tags": ",".join(tags),
            "bottom_divergence": bottom_div is not None,
            "summary": self._format_review_summary(
                name or code, code, trend, profit_pct, action, rsi, support, resistance
            ),
        }

    def _format_review_summary(
        self,
        name: str,
        code: str,
        trend: str,
        profit_pct: float,
        action: str,
        rsi: float,
        support: float,
        resistance: float,
    ) -> str:
        pnl = f"浮盈 {profit_pct:+.1f}%" if profit_pct else ""
        return (
            f"【{name}({code})】{trend} | {pnl} | 建议{action} | "
            f"RSI {rsi} | 支撑 {support} / 阻力 {resistance}"
        )

    def review_holdings(self, positions: List[dict], show_progress: bool = False) -> List[dict]:
        """逐只持仓中线复盘。"""
        reviews = []
        total = len(positions)
        for i, p in enumerate(positions, 1):
            if show_progress:
                _progress(f"  复盘 {p.get('name', p.get('code'))}({p.get('code')}) [{i}/{total}]", True)
            reviews.append(
                self.analyze_stock(
                    code=p["code"],
                    name=p.get("name", ""),
                    cost_price=float(p.get("cost_price", 0)),
                    weight_pct=float(p.get("weight_pct", 0)),
                )
            )
        reviews.sort(key=lambda x: x.get("midterm_score", 0) if x.get("ok") else -1, reverse=True)
        return reviews

    def optimize_positions(
        self,
        portfolio_stats: dict,
        reviews: Optional[List[dict]] = None,
    ) -> dict:
        """持仓优化：目标仓位、加减仓、现金比例。"""
        if not portfolio_stats.get("has_data"):
            return {"ok": False, "suggestions": ["暂无实盘持仓"]}

        positions = [
            p for p in portfolio_stats.get("positions", [])
            if p.get("bucket", _classify_bucket(p.get("strategy", ""))) == "midterm"
        ]
        if not positions:
            return {
                "ok": True,
                "ideal_weight_pct": 0,
                "invested_pct": 0,
                "suggestions": ["中线账户暂无持仓，可在 15 万额度内布局 3-6 只。"],
                "actions": [],
            }

        mid_bucket = portfolio_stats.get("buckets", {}).get("midterm", {})
        bucket_capital = float(mid_bucket.get("capital", portfolio_stats.get("midterm_capital", 150000)))
        invested_pct = float(mid_bucket.get("invested_pct", 0))
        reviews = reviews or self.review_holdings(positions)
        review_map = {r["code"]: r for r in reviews if r.get("ok")}

        count = len(positions)
        min_pos, max_pos = self.target_position_count
        ideal_weight = 100 / max(count, min_pos)

        suggestions: List[str] = []
        actions: List[dict] = []

        suggestions.append(
            f"中线账户（额度 {bucket_capital/10000:.1f} 万）：{count} 只持仓，"
            f"已用 {invested_pct:.0f}%，建议 {min_pos}-{max_pos} 只、单票≤{self.max_single_weight:.0f}%。"
        )

        if invested_pct > 95:
            suggestions.append("中线账户接近满仓，建议保留 10%-15% 现金应对回调。")
        elif invested_pct < 55 and count < max_pos:
            suggestions.append(
                f"中线账户仅使用 {invested_pct:.0f}%，可择优布局 {max_pos - count} 只标的。"
            )

        for p in positions:
            code = str(p["code"]).zfill(6)
            r = review_map.get(code, {})
            weight = float(p.get("weight_pct", 0))
            name = p.get("name", code)
            trend = r.get("trend", "未知")
            action = r.get("action", "观望")

            if weight > self.max_single_weight:
                reduce_to = self.max_single_weight * 0.9
                suggestions.append(
                    f"【减仓】{name} 占比 {weight:.1f}% 超限，"
                    f"反弹时减至约 {reduce_to:.0f}% 以内。"
                )
                actions.append({
                    "code": code, "name": name, "type": "reduce",
                    "reason": "单票占比过高", "target_weight_pct": reduce_to,
                })

            if trend == "空头" and float(p.get("profit_pct", 0)) < -5:
                suggestions.append(
                    f"【减仓】{name} 趋势空头且浮亏，中线逻辑偏弱，优先处理。"
                )
                actions.append({
                    "code": code, "name": name, "type": "reduce",
                    "reason": "趋势转弱", "target_weight_pct": max(weight * 0.5, 10),
                })

            if trend == "多头" and action == "持有" and weight < ideal_weight * 0.7:
                suggestions.append(
                    f"【可加仓】{name} 趋势良好但仓位偏轻（{weight:.1f}%），"
                    f"可考虑小幅加仓至约 {ideal_weight:.0f}%。"
                )
                actions.append({
                    "code": code, "name": name, "type": "add",
                    "reason": "趋势良好仓位轻", "target_weight_pct": round(ideal_weight, 1),
                })

        weak = [r for r in reviews if r.get("ok") and r.get("trend") == "空头"]
        strong = [r for r in reviews if r.get("ok") and r.get("trend") == "多头"]
        if len(weak) >= 2 and strong:
            suggestions.append(
                f"持仓分化：{len(strong)} 只多头 vs {len(weak)} 只空头，"
                "优先减弱势、保留强势，勿平均补仓。"
            )

        return {
            "ok": True,
            "ideal_weight_pct": round(ideal_weight, 1),
            "invested_pct": invested_pct,
            "suggestions": suggestions,
            "actions": actions,
        }

    def _score_candidate(
        self,
        code: str,
        name: str,
        spot: Optional[dict] = None,
        tuning: Optional[SelectionTuning] = None,
        *,
        check_60m: bool = True,
    ) -> Optional[dict]:
        hist = get_stock_hist(code, days=120)
        if hist.empty or len(hist) < DIVERGENCE_N_DAILY + 5:
            return None

        hist = hist.sort_values("date").reset_index(drop=True)
        spot_pct = float(spot.get("pct", 0)) if spot else 0.0
        turnover = float(spot.get("turnover", 0)) if spot else 0.0
        market_cap_yi = spot.get("market_cap_yi") if spot else None
        daily_amount = spot.get("amount") if spot else None
        if daily_amount is None and "amount" in hist.columns:
            amt = pd.to_numeric(hist["amount"], errors="coerce")
            if amt.notna().any():
                daily_amount = float(amt.iloc[-1])

        if spot_pct == 0 and "pct_chg" in hist.columns:
            last_pct = hist["pct_chg"].iloc[-1]
            if pd.notna(last_pct):
                spot_pct = float(last_pct)

        tech = _evaluate_midterm_technicals(
            hist, spot_pct, turnover, tuning=tuning,
            name=name, daily_amount=daily_amount, code=code,
            check_60m=check_60m,
        )
        if tech is None:
            return None

        if market_cap_yi is None:
            cap = None
        else:
            cap = float(market_cap_yi)
            if cap < self.min_recommend_market_cap or cap >= self.max_recommend_market_cap:
                return None
        if tech["price"] >= self.max_recommend_price:
            return None

        pe = spot.get("pe") if spot else None
        profit_yoy = spot.get("profit_yoy") if spot else None
        industry = spot.get("industry") if spot else None

        cond_labels = [_CONDITION_LABELS.get(c, c) for c in tech["conditions"]]
        yoy_part = ""
        if profit_yoy is not None and pd.notna(profit_yoy):
            yoy_part = f"，净利同比{float(profit_yoy):+.1f}%"

        cap_part = f"市值{cap:.0f}亿" if cap is not None else "市值—"
        reason = (
            f"{' · '.join(tech['tags'][:5])}；{tech.get('hold_style', '')}；"
            f"{tech.get('entry_hint', '')}；"
            f"MA60{tech.get('ma60_trend', '')} 20日{tech['ret_20d']:+.1f}% "
            f"60日{tech.get('ret_60d', 0):+.1f}% RSI{tech['rsi']:.0f}；"
            f"{cap_part}{yoy_part}"
        )

        return {
            "code": code,
            "name": name,
            "price": round(tech["price"], 2),
            "market_cap_yi": json_safe_float(cap, digits=1),
            "pct_chg": json_safe_float(spot_pct, digits=2),
            "turnover": json_safe_float(turnover, digits=2),
            "midterm_score": tech["score"],
            "trend": tech["trend"],
            "rsi": tech["rsi"],
            "ret_20d": tech["ret_20d"],
            "ret_60d": tech.get("ret_60d", 0),
            "ma5": tech["ma5"],
            "ma10": tech["ma10"],
            "ma20": tech["ma20"],
            "ma60": tech.get("ma60"),
            "bottom_divergence": tech.get("bottom_divergence", False),
            "hold_style": tech.get("hold_style", ""),
            "entry_hint": tech.get("entry_hint", ""),
            "ma60_trend": tech.get("ma60_trend", ""),
            "stop_confirm": tech.get("stop_confirm", False),
            "pe": json_safe_float(pe, digits=2),
            "profit_yoy": json_safe_float(profit_yoy, digits=2),
            "industry": industry or "",
            "tags": ",".join(tech["tags"]),
            "conditions": tech["conditions"],
            "condition_labels": cond_labels,
            "reason": reason,
        }

    def recommend_stocks(
        self,
        exclude_codes: Optional[List[str]] = None,
        top_n: int = 20,
        prefilter: int = MIDTERM_PREFILTER_DEFAULT,
        show_progress: bool = False,
        industry: Optional[str] = None,
        performance: Optional[str] = None,
        early_stop_pass: int = 0,
        tuning: Optional[SelectionTuning] = None,
        max_workers: int = MIDTERM_SCAN_WORKERS,
    ) -> Tuple[pd.DataFrame, dict]:
        """中线个股推荐（排除已持仓，支持行业/业绩筛选）。返回 (DataFrame, 筛选统计)。"""
        if tuning is None:
            tuning = build_selection_tuning()
        if show_progress and tuning.notes:
            _progress(format_tuning_summary(tuning), show_progress)
        select_stats: dict = {
            "market_total": 0,
            "prefilter_count": 0,
            "scored_pass": 0,
            "scored_fail": 0,
            "scored_errors": 0,
            "scored_stopped_early": False,
            "excluded_held": 0,
            "filter": {},
            "fallback_used": False,
            "tuning": tuning.to_dict(),
        }
        exclude = {str(c).zfill(6) for c in (exclude_codes or [])}
        industry = (industry or "").strip() or None
        performance = (performance or "").strip() or None
        if industry:
            _progress(f"  筛选行业: {industry}", show_progress)
        if performance:
            label = PERFORMANCE_FILTER_OPTIONS.get(performance, performance)
            _progress(f"  筛选业绩: {label}", show_progress)

        _progress("  拉取基本面(PE/净利同比)…", show_progress)
        fundamental_map = get_fundamental_map()

        _progress("  拉取全市场行情…", show_progress)
        market = get_market_spot(verbose=show_progress, force_refresh=False)
        if market.empty:
            _progress("  行情为空，跳过推荐", show_progress)
            return pd.DataFrame(), select_stats

        select_stats["market_total"] = len(market)

        code_col = get_stock_code_column(market)
        name_col = get_stock_name_column(market)
        df = exclude_bse_from_df(market.copy(), code_col)
        if df.empty:
            _progress("  剔除北交所后无可用标的", show_progress)
            return pd.DataFrame(), select_stats
        pct_col = next((c for c in ("pct_chg", "changepercent", "涨跌幅") if c in df.columns), None)
        turnover_col = next((c for c in ("turnover", "turnoverratio", "换手率") if c in df.columns), None)

        if pct_col:
            df["_pct"] = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
        else:
            df["_pct"] = 0
        if turnover_col:
            df["_turnover"] = pd.to_numeric(df[turnover_col], errors="coerce").fillna(0)
        else:
            df["_turnover"] = 0
        amount_col = next((c for c in ("amount", "成交额") if c in df.columns), None)
        if amount_col:
            df["_amount"] = pd.to_numeric(df[amount_col], errors="coerce").fillna(0)
            df["_amount_wan"] = df["_amount"].map(_amount_to_wan)
        else:
            df["_amount"] = 0.0
            df["_amount_wan"] = 0.0

        df["_price"] = _spot_price_series(df)
        price_ok = (df["_price"] > 0) & (df["_price"] < self.max_recommend_price)
        has_cap = "market_cap" in df.columns
        if has_cap:
            df["_cap_yi"] = _market_cap_to_yi(df["market_cap"])
            cap_ok = (
                (df["_cap_yi"] >= self.min_recommend_market_cap)
                & (df["_cap_yi"] < self.max_recommend_market_cap)
            )
        else:
            df["_cap_yi"] = float("nan")
            cap_ok = pd.Series(True, index=df.index)
            _progress("  行情无市值列，评分阶段再校验市值", show_progress)

        n_price = int(price_ok.sum())
        n_cap = int(cap_ok.sum()) if has_cap else len(df)
        n_liq = int((df.loc[price_ok & cap_ok, "_amount_wan"] >= MIN_DAILY_AMOUNT_WAN).sum()) if has_cap else 0
        _progress(
            f"  [初筛] 全市场 {len(df)} 只；股价<{self.max_recommend_price}元: {n_price}；"
            f"市值{self.min_recommend_market_cap:.0f}-{self.max_recommend_market_cap:.0f}亿: {n_cap}；"
            f"成交额≥{MIN_DAILY_AMOUNT_WAN}万: {n_liq}",
            show_progress,
        )

        def _build_candidates(limit: int) -> pd.DataFrame:
            mask = price_ok & cap_ok
            pool = df[mask].copy()
            if name_col and name_col in pool.columns:
                pool = pool[~pool[name_col].astype(str).map(_is_st_or_delist_name)]
            if pool.empty:
                return pool
            # 趋势回调候选：避免优先今日大跌，偏好温和调整 + 高流动性
            crash = pool["_pct"].clip(upper=0)
            mild = ((pool["_pct"] >= -4) & (pool["_pct"] <= 2)).astype(float)
            pool["_rank"] = (
                mild * 0.35
                + crash.clip(lower=-8) * 0.12   # 大跌日降权（crash 为负）
                + pool["_amount_wan"].clip(0, 120000) / 120000 * 0.38
                + (10 - pool["_turnover"].clip(0, 15)) / 10 * 0.15
            )
            return pool.sort_values("_rank", ascending=False).head(limit)

        scan_limit = max(prefilter, MIDTERM_PREFILTER_DEFAULT)
        candidates = _build_candidates(scan_limit)
        if candidates.empty and has_cap:
            _progress("  初筛为空，放宽市值下限…", show_progress)
            cap_ok = (
                (df["_cap_yi"] >= max(self.min_recommend_market_cap - 30, 80))
                & (df["_cap_yi"] < self.max_recommend_market_cap)
            )
            candidates = _build_candidates(scan_limit)

        select_stats["prefilter_count"] = len(candidates)
        select_stats["prefilter_limit"] = scan_limit
        select_stats["scan_workers"] = max_workers
        _progress(
            f"  初筛 {len(candidates)} 只（上限 {scan_limit}），"
            f"多线程评分中（{max_workers} 线程）…",
            show_progress,
        )

        def _scan_rows(rows: pd.DataFrame) -> Tuple[List[dict], int, int, int]:
            """并行技术面评分（全市场扫描关闭 60 分确认以提速）。"""
            tasks: List[dict] = []
            excluded = 0
            for _, row in rows.iterrows():
                code = str(row[code_col]).zfill(6)
                if is_bse_code(code):
                    continue
                if code in exclude:
                    excluded += 1
                    continue
                name = str(row[name_col]) if name_col else code
                cap_yi = row["_cap_yi"]
                fund = fundamental_map.get(code, {})
                tasks.append({
                    "code": code,
                    "name": name,
                    "spot": {
                        "pct": row["_pct"],
                        "turnover": row["_turnover"],
                        "amount": float(row["_amount"]) if pd.notna(row.get("_amount")) else None,
                        "market_cap_yi": float(cap_yi) if pd.notna(cap_yi) else None,
                        "pe": fund.get("pe"),
                        "profit_yoy": fund.get("profit_yoy"),
                    },
                })

            local_results: List[dict] = []
            fail = errors = 0
            total_local = len(tasks)
            if total_local == 0:
                return local_results, excluded, fail, errors

            lock = threading.Lock()
            done = 0
            stop_early = threading.Event()

            def _score_one(task: dict) -> tuple[str, Optional[dict], Optional[Exception]]:
                if stop_early.is_set():
                    return "skip", None, None
                try:
                    item = self._score_candidate(
                        task["code"],
                        task["name"],
                        task["spot"],
                        tuning=tuning,
                        check_60m=False,
                    )
                    return ("hit" if item else "miss"), item, None
                except Exception as exc:
                    return "err", None, exc

            workers = max(1, min(max_workers, total_local))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_score_one, t) for t in tasks]
                for future in as_completed(futures):
                    status, item, exc = future.result()
                    with lock:
                        done += 1
                        if status == "hit" and item:
                            local_results.append(item)
                        elif status == "miss":
                            fail += 1
                        elif status == "err":
                            errors += 1
                            if errors <= 3 and exc is not None:
                                _progress(f"  评分异常: {exc}", show_progress)

                        if show_progress and (
                            done == 1 or done % 25 == 0 or done == total_local
                        ):
                            _progress(
                                f"  评分进度 {done}/{total_local}（已命中 {len(local_results)}，"
                                f"未过线 {fail}"
                                + (f"，异常 {errors}" if errors else "")
                                + "）",
                                True,
                            )

                        if (
                            early_stop_pass > 0
                            and len(local_results) >= early_stop_pass
                            and done >= min(50, max(total_local // 3, 30))
                        ):
                            select_stats["scored_stopped_early"] = True
                            stop_early.set()
                            _progress(
                                f"  已命中 {len(local_results)} 只，提前结束评分（{done}/{total_local}）",
                                show_progress,
                            )

            return local_results, excluded, fail, errors

        results, excluded_held, scored_fail, scored_errors = _scan_rows(candidates)

        if not results:
            full_limit = min(int((price_ok & cap_ok).sum()), MIDTERM_PREFILTER_MAX)
            if full_limit > len(candidates):
                _progress(
                    f"  初筛未命中，扩大扫描至 {full_limit} 只…",
                    show_progress,
                )
                expanded = _build_candidates(full_limit)
                seen_idx = set(candidates.index)
                extra = expanded[~expanded.index.isin(seen_idx)]
                if extra.empty:
                    extra = expanded
                more_results, ex2, fail2, err2 = _scan_rows(extra)
                results.extend(more_results)
                excluded_held += ex2
                scored_fail += fail2
                scored_errors += err2
                select_stats["expanded_scan"] = True
                select_stats["prefilter_count"] = full_limit

        select_stats["scored_pass"] = len(results)
        select_stats["scored_fail"] = scored_fail
        select_stats["scored_errors"] = scored_errors
        select_stats["excluded_held"] = excluded_held
        _progress(
            f"  [评分] 技术面命中 {len(results)}/{select_stats['prefilter_count'] - excluded_held} 只"
            f"（未过线 {scored_fail}，已持仓排除 {excluded_held}）",
            show_progress,
        )

        if not results:
            _progress("  无符合条件的推荐标的", show_progress)
            return pd.DataFrame(), select_stats

        results.sort(key=lambda x: x["midterm_score"], reverse=True)
        if industry:
            enrich_codes = [r["code"] for r in results]
            _progress(f"  拉取行业 {len(enrich_codes)} 只（用于行业筛选）…", show_progress)
        else:
            enrich_codes = [r["code"] for r in results[: max(top_n * 3, 40)]]
        industry_map = ensure_industry_map(enrich_codes, verbose=show_progress)
        for item in results:
            item["industry"] = industry_map.get(item["code"], item.get("industry") or "")

        filtered, filter_stats = _apply_recommendation_filters(
            results, industry=industry, performance=performance,
        )
        select_stats["filter"] = filter_stats
        if industry or performance:
            _progress(f"  [筛选] 合计: {len(results)} → {len(filtered)} 只", show_progress)
        if not filtered and results and (industry or performance):
            _progress("  行业/业绩无匹配，回退展示全部技术命中标的", show_progress)
            filtered = results
            select_stats["fallback_used"] = True
        if not filtered:
            _progress("  无推荐标的，可尝试重置行业/业绩筛选", show_progress)
            return pd.DataFrame(), select_stats

        out = pd.DataFrame(filtered).head(top_n)
        _progress(
            f"  推荐命中 {len(out)} 只"
            + (f"（回退模式，未应用行业/业绩）" if select_stats["fallback_used"] else ""),
            show_progress,
        )
        if filter_stats.get("top_industries"):
            _progress(f"  技术命中行业分布: {', '.join(filter_stats['top_industries'])}", show_progress)
        return out.reset_index(drop=True), select_stats

    def run_quick_advice(self, portfolio_stats: dict) -> dict:
        """轻量分析：仅中线持仓复盘 + 优化（不扫全市场推荐）。"""
        all_positions = portfolio_stats.get("positions", [])
        midterm_positions = [
            p for p in all_positions
            if p.get("bucket", _classify_bucket(p.get("strategy", ""))) == "midterm"
        ]
        reviews = self.review_holdings(midterm_positions)
        optimization = self.optimize_positions(portfolio_stats, reviews)
        review_summaries = [r["summary"] for r in reviews if r.get("ok")]
        opt_suggestions = optimization.get("suggestions", [])
        daily_operations = build_daily_midterm_operations(
            portfolio_stats, reviews, optimization=optimization,
        )

        return {
            "generated_at": datetime.now().isoformat(),
            "style": "中线",
            "quick": True,
            "reviews": reviews,
            "optimization": optimization,
            "recommendations": [],
            "daily_operations": daily_operations,
            "suggestions": review_summaries + opt_suggestions + daily_operations.get("lines", []),
            "review_summaries": review_summaries,
            "optimize_suggestions": opt_suggestions,
        }

    def run_full_advice(
        self,
        portfolio_stats: dict,
        show_progress: bool = False,
        industry: Optional[str] = None,
        performance: Optional[str] = None,
    ) -> dict:
        """完整实盘中线分析：复盘 + 优化 + 推荐。"""
        _progress("=" * 50, show_progress)
        _progress("实盘中线分析开始", show_progress)
        all_positions = portfolio_stats.get("positions", [])
        midterm_positions = [
            p for p in all_positions
            if p.get("bucket", _classify_bucket(p.get("strategy", ""))) == "midterm"
        ]
        _progress(f"[1/4] 中线持仓复盘（{len(midterm_positions)} 只）", show_progress)
        reviews = self.review_holdings(midterm_positions, show_progress=show_progress)
        ok_n = sum(1 for r in reviews if r.get("ok"))
        _progress(f"  复盘完成：成功 {ok_n}/{len(reviews)}", show_progress)

        _progress("[2/4] 持仓优化建议", show_progress)
        optimization = self.optimize_positions(portfolio_stats, reviews)
        _progress(f"  优化建议 {len(optimization.get('suggestions', []))} 条", show_progress)

        held_codes = [p["code"] for p in all_positions]
        tuning = build_selection_tuning()
        _progress("[3/4] 全市场推荐扫描", show_progress)
        recommendations, select_stats = self.recommend_stocks(
            exclude_codes=held_codes,
            top_n=20,
            show_progress=show_progress,
            industry=industry,
            performance=performance,
            early_stop_pass=0,
            tuning=tuning,
        )

        _progress("[4/4] 生成报告", show_progress)
        review_summaries = [r["summary"] for r in reviews if r.get("ok")]
        opt_suggestions = optimization.get("suggestions", [])
        rec_records = df_to_records_safe(recommendations)

        all_text = review_summaries + opt_suggestions
        for r in rec_records[:5]:
            all_text.append(
                f"【推荐】{r['name']}({r['code']}) 评分{r['midterm_score']} "
                f"{r['reason']} 标签:{r.get('tags', '')}"
            )

        daily_operations = build_daily_midterm_operations(
            portfolio_stats,
            reviews,
            optimization=optimization,
            recommendations=rec_records,
        )

        result = {
            "generated_at": datetime.now().isoformat(),
            "style": "中线",
            "filters": {
                "industry": industry or "",
                "performance": performance or "",
            },
            "select_stats": select_stats,
            "select_conditions": get_midterm_select_conditions(),
            "reviews": reviews,
            "optimization": optimization,
            "recommendations": rec_records,
            "daily_operations": daily_operations,
            "suggestions": all_text + daily_operations.get("lines", []),
            "review_summaries": review_summaries,
            "optimize_suggestions": opt_suggestions,
        }

        path = OUTPUT_DIR / f"midterm_{datetime.now().strftime('%Y%m%d')}.json"
        path.write_text(
            json.dumps(
                sanitize_for_json({
                    **result,
                    "reviews": reviews,
                    "recommendations": rec_records,
                }),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        md = format_midterm_report_markdown(result)
        md_path = OUTPUT_DIR / f"midterm_{datetime.now().strftime('%Y%m%d')}.md"
        md_path.write_text(md, encoding="utf-8")
        result["markdown"] = md
        result["report_path"] = str(md_path)
        _progress(f"分析完成：推荐 {len(rec_records)} 只，报告已保存", show_progress)

        try:
            from quantpy.midterm_pick_tracker import run_midterm_tracker_cycle
            tracker = run_midterm_tracker_cycle(
                rec_records[:10], show_progress=show_progress,
            )
            result["pick_tracker"] = tracker
            if tracker.get("suggestions"):
                result["suggestions"].extend(tracker["suggestions"][:4])
        except Exception as exc:
            if show_progress:
                _progress(f"  跟进池记录跳过: {exc}", show_progress)

        return result


def build_daily_midterm_operations(
    portfolio_stats: dict,
    reviews: List[dict],
    optimization: Optional[dict] = None,
    recommendations: Optional[List[dict]] = None,
    level_alerts: Optional[List[dict]] = None,
) -> dict:
    """生成实盘中线每日操作建议（账户 + 逐股 + 优先级）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    optimization = optimization or {}
    recommendations = recommendations or []
    alert_list = level_alerts or []
    if isinstance(level_alerts, dict):
        alert_list = level_alerts.get("alerts", [])

    midterm_positions = [
        p for p in portfolio_stats.get("positions", [])
        if p.get("bucket", _classify_bucket(p.get("strategy", ""))) == "midterm"
    ]
    mid_bucket = portfolio_stats.get("buckets", {}).get("midterm", {})
    bucket_capital = float(mid_bucket.get("capital", portfolio_stats.get("midterm_capital", 150000)))
    invested_pct = float(mid_bucket.get("invested_pct", 0))

    review_map = {str(r["code"]).zfill(6): r for r in reviews if r.get("ok")}
    alert_map = {str(a["code"]).zfill(6): a for a in alert_list}
    held_codes = {str(p["code"]).zfill(6) for p in midterm_positions}

    opt_action_map: Dict[str, List[dict]] = {}
    for act in optimization.get("actions", []):
        code = str(act.get("code", "")).zfill(6)
        opt_action_map.setdefault(code, []).append(act)

    def _priority(action: str, alert: Optional[dict], profit_pct: float) -> int:
        if alert and alert.get("signal") == "sell":
            return 1
        if action in ("减仓/止损", "减仓观望"):
            return 2
        if alert and alert.get("signal") == "buy":
            return 3
        if action == "分批止盈":
            return 4
        return 6

    stock_operations: List[dict] = []
    for p in midterm_positions:
        code = str(p["code"]).zfill(6)
        r = review_map.get(code, {})
        alert = alert_map.get(code)
        action = r.get("action", "观望")
        profit_pct = float(p.get("profit_pct", r.get("profit_pct", 0)))
        weight_pct = float(p.get("weight_pct", r.get("weight_pct", 0)))
        reasons: List[str] = list(r.get("action_reasons") or [])

        for act in opt_action_map.get(code, []):
            t = act.get("type", "")
            if t == "reduce":
                reasons.append(f"仓位优化：建议减至约 {act.get('target_weight_pct')}%（{act.get('reason')}）")
            elif t == "add":
                reasons.append(f"仓位优化：可加仓至约 {act.get('target_weight_pct')}%（{act.get('reason')}）")

        if alert:
            reasons.insert(0, alert.get("message", alert.get("alert_label", "")))

        if not reasons and r.get("summary"):
            reasons.append(r["summary"])

        priority = _priority(action, alert, profit_pct)
        if any(a.get("type") == "reduce" for a in opt_action_map.get(code, [])):
            priority = min(priority, 2)
        if any(a.get("type") == "add" for a in opt_action_map.get(code, [])):
            priority = min(priority, 5)

        stock_operations.append({
            "code": code,
            "name": p.get("name", code),
            "action": action,
            "priority": priority,
            "trend": r.get("trend", "—"),
            "midterm_score": r.get("midterm_score"),
            "profit_pct": round(profit_pct, 2),
            "weight_pct": round(weight_pct, 2),
            "rsi": r.get("rsi"),
            "support": r.get("support"),
            "resistance": r.get("resistance"),
            "stop_suggest": r.get("stop_suggest"),
            "price": r.get("price"),
            "cost_price": p.get("cost_price"),
            "reasons": reasons,
            "detail": "；".join(reasons) if reasons else r.get("summary", ""),
            "level_alert": alert,
            "tags": r.get("tags", ""),
        })

    stock_operations.sort(key=lambda x: (x["priority"], -float(x.get("midterm_score") or 0)))

    overview: List[str] = []
    if not midterm_positions:
        overview.append("中线账户暂无持仓，可在 15 万额度内布局 3-6 只。")
    else:
        overview.append(
            f"中线账户 {bucket_capital/10000:.1f} 万 · 持仓 {len(midterm_positions)} 只 · "
            f"仓位 {invested_pct:.0f}%"
        )
        for s in optimization.get("suggestions", [])[:4]:
            if s not in overview:
                overview.append(s)

    urgent = [s for s in stock_operations if s["priority"] <= 2]
    if urgent:
        overview.append(f"今日优先处理 {len(urgent)} 只：{', '.join(s['name'] for s in urgent[:4])}")

    new_buy_hints = [
        {
            "code": str(r["code"]).zfill(6),
            "name": r.get("name", ""),
            "midterm_score": r.get("midterm_score"),
            "reason": r.get("reason", ""),
            "price": r.get("price"),
        }
        for r in recommendations
        if str(r.get("code", "")).zfill(6) not in held_codes
    ][:5]

    if new_buy_hints and invested_pct < 85:
        overview.append(
            f"可关注新开仓候选 {len(new_buy_hints)} 只（见下方推荐表）"
        )

    lines: List[str] = []
    for s in stock_operations:
        pnl = f"浮盈{s['profit_pct']:+.1f}%" if s.get("profit_pct") is not None else ""
        lines.append(
            f"【{s['action']}】{s['name']}({s['code']}) {s.get('trend', '')} {pnl} · {s.get('detail', '')[:80]}"
        )
    for h in new_buy_hints[:3]:
        lines.append(
            f"【可关注】{h['name']}({h['code']}) 评分{h.get('midterm_score')} · "
            f"{(h.get('reason') or '')[:60]}"
        )

    return {
        "date": today,
        "overview": overview,
        "account": {
            "capital": bucket_capital,
            "invested_pct": invested_pct,
            "position_count": len(midterm_positions),
            "market_value": float(mid_bucket.get("market_value", 0)),
        },
        "stock_operations": stock_operations,
        "new_buy_hints": new_buy_hints,
        "lines": lines,
    }


def ensure_daily_midterm_operations(
    midterm: dict,
    portfolio_stats: dict,
    level_alerts: Optional[dict] = None,
) -> dict:
    """缓存报告缺 daily_operations 时按复盘数据补全。"""
    if midterm.get("daily_operations"):
        return midterm["daily_operations"]
    reviews = midterm.get("reviews") or []
    if not reviews:
        return {}
    return build_daily_midterm_operations(
        portfolio_stats,
        reviews,
        optimization=midterm.get("optimization"),
        recommendations=midterm.get("recommendations"),
        level_alerts=level_alerts,
    )


def format_midterm_report_markdown(result: dict) -> str:
    """将中线分析结果格式化为 Markdown 报告。"""
    now = result.get("generated_at", datetime.now().isoformat())[:19].replace("T", " ")
    parts = [
        f"# 实盘中线分析报告\n",
        f"**生成时间**: {now}\n",
        f"---\n",
        f"## 一、持仓复盘\n\n",
    ]
    review_rows = []
    for r in result.get("reviews", []):
        if not r.get("ok"):
            continue
        review_rows.append([
            r["code"],
            r["name"],
            r["trend"],
            r["midterm_score"],
            f"{r.get('profit_pct', 0):+.2f}",
            r["rsi"],
            r.get("support", ""),
            r.get("resistance", ""),
            r["action"],
        ])
    if review_rows:
        parts.append(
            format_markdown_table(
                ["代码", "名称", "趋势", "评分", "浮盈%", "RSI", "支撑", "压力", "建议"],
                review_rows,
                aligns=["left", "left", "left", "right", "right", "right", "right", "right", "left"],
            )
        )
        parts.append("\n")
    else:
        parts.append("暂无中线持仓复盘数据。\n\n")

    daily = result.get("daily_operations") or {}
    if daily.get("stock_operations") or daily.get("overview"):
        parts.append("## 二、今日操作建议\n\n")
        for i, line in enumerate(daily.get("overview", []), 1):
            parts.append(f"{i}. {line}\n")
        parts.append("\n")
        op_rows = []
        for s in daily.get("stock_operations", []):
            op_rows.append([
                s["code"],
                s["name"],
                s.get("action", ""),
                s.get("trend", ""),
                f"{s.get('profit_pct', 0):+.1f}",
                s.get("rsi", ""),
                s.get("support", ""),
                s.get("resistance", ""),
                truncate_display(s.get("detail", ""), 36),
            ])
        if op_rows:
            parts.append(
                format_markdown_table(
                    ["代码", "名称", "操作", "趋势", "浮盈%", "RSI", "支撑", "压力", "说明"],
                    op_rows,
                    aligns=["left", "left", "left", "left", "right", "right", "right", "right", "left"],
                )
            )
            parts.append("\n")

    if result.get("optimize_suggestions"):
        parts.append("## 三、持仓优化\n\n")
        for i, s in enumerate(result["optimize_suggestions"], 1):
            parts.append(f"{i}. {s}\n")
        parts.append("\n")

    recs = result.get("recommendations", [])
    if recs:
        parts.append("## 四、个股推荐（MACD+OBV底背离 · 日线N=25 · 成交额≥5000万）\n\n")
        parts.append(
            "选股条件：" + " · ".join(c["label"] for c in MIDTERM_SELECT_CONDITIONS) + "\n\n"
        )
        rec_rows = [
            [
                r["code"],
                r["name"],
                r.get("industry") or "—",
                f"{r.get('pe', 0):.1f}" if r.get("pe") is not None else "—",
                f"{r.get('profit_yoy', 0):+.1f}%" if r.get("profit_yoy") is not None else "—",
                f"{r.get('price', 0):.2f}",
                f"{r.get('market_cap_yi', 0):.1f}" if r.get("market_cap_yi") is not None else "—",
                r["midterm_score"],
                f"{r.get('pct_chg', 0):.2f}",
                r.get("rsi", ""),
                truncate_display(r.get("reason", ""), 28),
            ]
            for r in recs
        ]
        parts.append(
            format_markdown_table(
                ["代码", "名称", "行业", "PE", "净利同比", "股价", "市值(亿)", "评分", "涨幅%", "RSI", "理由"],
                rec_rows,
                aligns=["left", "left", "left", "right", "right", "right", "right", "right", "right", "right", "left"],
            )
        )
        parts.append("\n")

    parts.append("---\n*仅供参考，不构成投资建议。*\n")
    return "".join(parts)


def load_latest_midterm_advice() -> dict:
    files = sorted(OUTPUT_DIR.glob("midterm_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return sanitize_for_json(json.loads(files[0].read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def run_midterm_advice(
    portfolio_stats: Optional[dict] = None,
    show_progress: bool = True,
    full: bool = True,
    industry: Optional[str] = None,
    performance: Optional[str] = None,
) -> dict:
    from quantpy.portfolio import PortfolioManager

    if portfolio_stats is None:
        portfolio_stats = PortfolioManager().analyze()
    advisor = MidtermPortfolioAdvisor()
    if full:
        return advisor.run_full_advice(
            portfolio_stats,
            show_progress=show_progress,
            industry=industry,
            performance=performance,
        )
    return advisor.run_quick_advice(portfolio_stats)
