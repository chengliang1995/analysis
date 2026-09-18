"""
中线选股：MA20 突破 + 5 日线回踩分批建仓（鱼身波段）

策略要点（用户分享）：
- 板块与个股站上 MA20 并完成突破
- 沿 5 日线向上时持有；每次回踩 MA5 为分批建仓点
- 获利 20~30% 撤出本金、利润继续走加速；上涨过程中不加仓
- 连续跌破 MA5 且 5 日线下穿 10 日线时清仓，只吃鱼身
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from quantpy.stock_data import (
    ensure_industry_map,
    exclude_bse_from_df,
    fetch_board_constituents,
    fetch_board_list,
    get_market_spot,
    get_stock_code_column,
    get_stock_hist,
    get_stock_name_column,
    is_bse_code,
)

MIN_DAILY_AMOUNT_WAN = 5000
MA20_PULLBACK_MIN_SCORE = 62
BREAKOUT_LOOKBACK = 15
# 回踩窗口略放宽，避免常态 0 命中（原约 ±1.5%）
MA5_TOUCH_LOW = 0.985
MA5_TOUCH_HIGH = 1.025
MA20_SLOPE_DAYS = 5
RIDE_MA5_DAYS = 4
PREFILTER_DEFAULT = 600
SCAN_WORKERS = 8

MA20_PULLBACK_SELECT_CONDITIONS = [
    {"id": "cap_range", "label": "市值150-1000亿", "category": "基本面"},
    {"id": "price_cap", "label": "股价<100元", "category": "基本面"},
    {"id": "liquidity", "label": "成交额≥5000万", "category": "基本面"},
    {"id": "sector_ma20", "label": "板块站上MA20并突破", "category": "板块"},
    {"id": "above_ma20", "label": "个股站上MA20", "category": "趋势"},
    {"id": "ma20_breakout", "label": "近期突破MA20", "category": "趋势"},
    {"id": "ma20_rising", "label": "MA20向上", "category": "趋势"},
    {"id": "ma5_above_ma10", "label": "MA5>MA10多头", "category": "趋势"},
    {"id": "ride_ma5", "label": "沿5日线向上", "category": "趋势"},
    {"id": "pullback_ma5", "label": "回踩MA5建仓点", "category": "买点"},
    {"id": "vol_shrink_pullback", "label": "回踩缩量", "category": "买点"},
    {"id": "no_chase_rally", "label": "非追涨(远离MA5)", "category": "纪律"},
]

_CONDITION_LABELS = {c["id"]: c["label"] for c in MA20_PULLBACK_SELECT_CONDITIONS}


def get_ma20_pullback_select_conditions() -> List[dict]:
    return list(MA20_PULLBACK_SELECT_CONDITIONS)


def _progress(msg: str, show: bool) -> None:
    if show:
        print(msg, flush=True)


def _is_st_or_delist_name(name: str) -> bool:
    n = str(name or "").upper()
    return "ST" in n or "退" in str(name or "")


def _amount_to_wan(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v >= 1e7:
        return v / 10000.0
    return v


def _safe_pct(a: float, b: float) -> float:
    if not b:
        return 0.0
    return (a / b - 1) * 100


def _compute_mas(close: pd.Series) -> dict:
    return {
        "ma5": float(close.rolling(5).mean().iloc[-1]) if len(close) >= 5 else float(close.iloc[-1]),
        "ma10": float(close.rolling(10).mean().iloc[-1]) if len(close) >= 10 else float(close.iloc[-1]),
        "ma20": float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else float(close.iloc[-1]),
    }


def _ma20_breakout(close: pd.Series, ma20: pd.Series, lookback: int = BREAKOUT_LOOKBACK) -> bool:
    """近 lookback 日内由 MA20 下突破至上，且已站稳至少 2 日。"""
    if len(close) < lookback + 22:
        return False
    above = close > ma20
    if not bool(above.iloc[-1]) or not bool(above.iloc[-2]):
        return False
    past = above.iloc[-lookback - 2 : -2]
    if past.empty:
        return False
    return bool((~past).any())


def _riding_ma5(close: pd.Series, days: int = RIDE_MA5_DAYS) -> bool:
    """突破后沿 5 日线向上：近 days 日多数收在 MA5 上方且 MA5 上行。"""
    if len(close) < days + 8:
        return False
    seg = close.iloc[-days - 1 : -1]
    ma5 = close.rolling(5).mean()
    ma5_seg = ma5.iloc[-days - 1 : -1]
    above_cnt = sum(float(seg.iloc[i]) >= float(ma5_seg.iloc[i]) * 0.998 for i in range(len(seg)))
    ma5_rise = float(ma5.iloc[-2]) > float(ma5.iloc[-days - 2])
    return above_cnt >= max(2, days - 1) and ma5_rise


def _pullback_to_ma5(price: float, ma5: float) -> bool:
    if ma5 <= 0:
        return False
    ratio = price / ma5
    return MA5_TOUCH_LOW <= ratio <= MA5_TOUCH_HIGH


def _volume_shrink(hist: pd.DataFrame) -> bool:
    if "volume" not in hist.columns or len(hist) < 6:
        return False
    vol = pd.to_numeric(hist["volume"], errors="coerce")
    today = float(vol.iloc[-1])
    avg5 = float(vol.iloc[-6:-1].mean())
    return avg5 > 0 and today < avg5 * 0.85


def build_hot_sector_codes(
    *,
    top_boards: int = 20,
    show_progress: bool = False,
) -> Tuple[Set[str], Set[str]]:
    """
    热门行业板块：当日涨幅靠前且板块指数站上 MA20 的成份股代码。
    返回 (hot_codes, hot_industry_names)。
    """
    hot_codes: Set[str] = set()
    hot_names: Set[str] = set()
    boards = fetch_board_list("industry", force_refresh=False, verbose=False)
    if not boards:
        boards = fetch_board_list("industry", force_refresh=True, verbose=False)
    if not boards:
        _progress("  [MA20] 板块列表不可用，跳过板块过滤", show_progress)
        return hot_codes, hot_names

    ranked = sorted(boards, key=lambda b: float(b.get("pct_chg") or 0), reverse=True)
    for board in ranked[: max(1, top_boards)]:
        pct = float(board.get("pct_chg") or 0)
        if pct < 0:
            continue
        bname = str(board.get("name") or "").strip()
        bcode = str(board.get("code") or "").strip()
        if not bcode:
            continue
        hot_names.add(bname)
        members = fetch_board_constituents(bcode, verbose=False)
        for m in members:
            code = str(m.get("code") or "").zfill(6)
            if code and not is_bse_code(code):
                hot_codes.add(code)
    _progress(
        f"  [MA20] 热门板块 {len(hot_names)} 个 · 成份 {len(hot_codes)} 只",
        show_progress,
    )
    return hot_codes, hot_names


def evaluate_ma20_pullback_technicals(
    hist: pd.DataFrame,
    spot_pct: float,
    *,
    name: str = "",
    daily_amount: Optional[float] = None,
    code: str = "",
    sector_hot: bool = False,
    tuning=None,
) -> Optional[dict]:
    """单票 MA20 突破 + MA5 回踩评分。"""
    del code
    if _is_st_or_delist_name(name):
        return None
    if daily_amount is not None and daily_amount > 0:
        if _amount_to_wan(daily_amount) < MIN_DAILY_AMOUNT_WAN:
            return None
    if spot_pct <= -9.5:
        return None
    if hist.empty or len(hist) < 35:
        return None

    hist = hist.sort_values("date").reset_index(drop=True)
    close = pd.to_numeric(hist["close"], errors="coerce")
    if close.isna().iloc[-1]:
        return None

    price = float(close.iloc[-1])
    ma20_series = close.rolling(20).mean()
    mas = _compute_mas(close)
    ma5, ma10, ma20 = mas["ma5"], mas["ma10"], mas["ma20"]

    conditions: List[str] = ["cap_range", "price_cap", "liquidity"]
    tags: List[str] = []

    if ma20 <= 0 or price <= ma20:
        return None
    conditions.extend(["above_ma20"])

    ma20_prev = float(ma20_series.iloc[-1 - MA20_SLOPE_DAYS]) if len(ma20_series) > MA20_SLOPE_DAYS else ma20
    if ma20 <= ma20_prev:
        return None
    conditions.append("ma20_rising")
    tags.append("MA20向上")

    if not _ma20_breakout(close, ma20_series):
        return None
    conditions.append("ma20_breakout")
    tags.append("突破MA20")

    if not sector_hot:
        return None
    conditions.append("sector_ma20")
    tags.append("板块共振")

    if ma5 <= ma10:
        return None
    conditions.append("ma5_above_ma10")
    tags.append("MA5>MA10")

    if not _riding_ma5(close):
        return None
    conditions.append("ride_ma5")
    tags.append("沿5日线")

    if not _pullback_to_ma5(price, ma5):
        return None
    conditions.append("pullback_ma5")
    tags.append("回踩MA5")

    vol_shrink = _volume_shrink(hist)
    if vol_shrink:
        conditions.append("vol_shrink_pullback")
        tags.append("回踩缩量")

    # 远离 MA5 追涨不加仓
    if price > ma5 * 1.03:
        return None
    conditions.append("no_chase_rally")

    ret_20d = _safe_pct(price, float(close.iloc[-21])) if len(close) >= 21 else 0.0
    ret_60d = _safe_pct(price, float(close.iloc[-61])) if len(close) >= 61 else 0.0

    score = 58
    if vol_shrink:
        score += 10
    if ma5 > ma10 > ma20:
        score += 8
        tags.append("均线多头")
    if -2 <= spot_pct <= 1.5:
        score += 6
    elif spot_pct > 4:
        score -= 8
        tags.append("当日偏热")

    if tuning:
        for cond in conditions:
            score += tuning.midterm_condition_bonus.get(cond, 0)
            score -= tuning.midterm_condition_penalty.get(cond, 0)
        for tag_key, bonus in tuning.midterm_tag_bonus.items():
            if any(tag_key in t or t == tag_key for t in tags):
                score += bonus
        # MA20 回踩用独立门槛，勿套用中线跟进抬到的 midterm_min_score(80)
        min_score = int(
            getattr(tuning, "ma20_pullback_min_score", None) or MA20_PULLBACK_MIN_SCORE
        )
    else:
        min_score = MA20_PULLBACK_MIN_SCORE

    if score < min_score:
        return None

    # 数据驱动：拒 70–80 弱档，保留 [min,70) 与 80+
    reject_bands = []
    if tuning is not None:
        reject_bands = list(getattr(tuning, "midterm_reject_score_bands", None) or [])
    for band in reject_bands:
        if not isinstance(band, (list, tuple)) or len(band) < 2:
            continue
        lo, hi = float(band[0]), float(band[1])
        if lo <= score < hi:
            return None

    hold_style = "沿5日线持有；回踩MA5分批，上涨不加仓"
    entry_hint = f"MA5附近分批建仓({ma5:.2f})；获利20~30%撤本金"
    trend = "MA20突破后MA5回踩"
    if vol_shrink:
        entry_hint += "；缩量回踩优先"

    return {
        "score": score,
        "tags": tags,
        "conditions": conditions,
        "price": price,
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "ma60": round(float(close.rolling(60).mean().iloc[-1]), 2) if len(close) >= 60 else None,
        "rsi": 0.0,
        "ret_20d": round(ret_20d, 2),
        "ret_60d": round(ret_60d, 2),
        "trend": trend,
        "hold_style": hold_style,
        "entry_hint": entry_hint,
        "ma60_trend": "",
        "bottom_divergence": False,
        "stop_confirm": True,
        "strategy": "ma20_pullback",
    }


def run_ma20_pullback_market_scan(
    *,
    exclude_codes: Optional[List[str]] = None,
    top_n: int = 20,
    prefilter: int = PREFILTER_DEFAULT,
    show_progress: bool = False,
    industry: Optional[str] = None,
    tuning=None,
) -> Tuple[List[dict], dict]:
    """全市场扫描 MA20 突破 + MA5 回踩标的（供模拟中线 / 手动触发）。"""
    from quantpy.selection_tuning import build_selection_tuning

    if tuning is None:
        tuning = build_selection_tuning()

    exclude = {str(c).zfill(6) for c in (exclude_codes or [])}
    stats: dict = {
        "strategy": "ma20_pullback",
        "market_total": 0,
        "prefilter_count": 0,
        "scored_pass": 0,
        "sector_hot_count": 0,
        "excluded_held": 0,
    }

    hot_codes, hot_names = build_hot_sector_codes(show_progress=show_progress)
    stats["sector_hot_count"] = len(hot_codes)

    market = get_market_spot(verbose=show_progress, force_refresh=False)
    if market.empty:
        return [], stats

    code_col = get_stock_code_column(market)
    name_col = get_stock_name_column(market)
    df = exclude_bse_from_df(market.copy(), code_col)
    stats["market_total"] = len(df)

    pct_col = next((c for c in ("pct_chg", "changepercent", "涨跌幅") if c in df.columns), None)
    amount_col = next((c for c in ("amount", "成交额") if c in df.columns), None)
    if pct_col:
        df["_pct"] = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
    else:
        df["_pct"] = 0.0
    if amount_col:
        df["_amount_wan"] = pd.to_numeric(df[amount_col], errors="coerce").fillna(0).map(_amount_to_wan)
    else:
        df["_amount_wan"] = 0.0

    price_col = next((c for c in ("close", "price", "最新价") if c in df.columns), None)
    if price_col:
        df["_price"] = pd.to_numeric(df[price_col], errors="coerce").fillna(0)
    else:
        df["_price"] = 0.0

    pool = df[(df["_price"] > 0) & (df["_price"] < 100)].copy()
    if name_col and name_col in pool.columns:
        pool = pool[~pool[name_col].astype(str).map(_is_st_or_delist_name)]
    pool = pool[pool["_amount_wan"] >= MIN_DAILY_AMOUNT_WAN]
    if hot_codes:
        pool["_hot"] = pool[code_col].astype(str).str.zfill(6).isin(hot_codes)
        pool = pool[pool["_hot"]]
    if industry:
        imap = ensure_industry_map(pool[code_col].astype(str).str.zfill(6).tolist(), verbose=False)
        pool["_ind"] = pool[code_col].astype(str).str.zfill(6).map(imap).fillna("")
        pool = pool[pool["_ind"] == industry.strip()]

    mild = ((pool["_pct"] >= -3) & (pool["_pct"] <= 2)).astype(float)
    pool["_rank"] = mild * 0.4 + pool["_amount_wan"].clip(0, 80000) / 80000 * 0.6
    candidates = pool.sort_values("_rank", ascending=False).head(prefilter)
    stats["prefilter_count"] = len(candidates)

    _progress(f"  [MA20回踩] 初筛 {len(candidates)} 只，评分中…", show_progress)

    tasks = []
    for _, row in candidates.iterrows():
        code = str(row[code_col]).zfill(6)
        if is_bse_code(code) or code in exclude:
            if code in exclude:
                stats["excluded_held"] += 1
            continue
        name = str(row[name_col]) if name_col else code
        tasks.append({
            "code": code,
            "name": name,
            "pct": float(row["_pct"]),
            "amount": float(row.get(amount_col, 0) or 0) if amount_col else None,
            "sector_hot": code in hot_codes if hot_codes else True,
        })

    results: List[dict] = []
    lock = threading.Lock()
    done = 0

    def _score_one(task: dict) -> Optional[dict]:
        hist = get_stock_hist(task["code"], days=90, patch_live=True)
        if hist.empty or len(hist) < 35:
            return None
        tech = evaluate_ma20_pullback_technicals(
            hist,
            task["pct"],
            name=task["name"],
            daily_amount=task["amount"],
            code=task["code"],
            sector_hot=task["sector_hot"],
            tuning=tuning,
        )
        if tech is None:
            return None
        cond_labels = [_CONDITION_LABELS.get(c, c) for c in tech["conditions"]]
        reason = (
            f"{' · '.join(tech['tags'][:4])}；{tech.get('hold_style', '')}；"
            f"{tech.get('entry_hint', '')}；MA5{tech['ma5']} MA20{tech['ma20']}"
        )
        return {
            "code": task["code"],
            "name": task["name"],
            "price": round(tech["price"], 2),
            "pct_chg": round(task["pct"], 2),
            "midterm_score": tech["score"],
            "trend": tech["trend"],
            "ma5": tech["ma5"],
            "ma10": tech["ma10"],
            "ma20": tech["ma20"],
            "ret_20d": tech["ret_20d"],
            "tags": ",".join(tech["tags"]),
            "conditions": tech["conditions"],
            "condition_labels": cond_labels,
            "hold_style": tech["hold_style"],
            "entry_hint": tech["entry_hint"],
            "reason": reason,
            "strategy": "ma20_pullback",
        }

    workers = max(1, min(SCAN_WORKERS, len(tasks) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_score_one, t) for t in tasks]
        for future in as_completed(futures):
            item = future.result()
            with lock:
                done += 1
                if item:
                    results.append(item)
                if show_progress and (done == 1 or done % 20 == 0 or done == len(tasks)):
                    _progress(
                        f"  [MA20回踩] 进度 {done}/{len(tasks)} · 命中 {len(results)}",
                        True,
                    )

    results.sort(key=lambda x: x.get("midterm_score", 0), reverse=True)
    stats["scored_pass"] = len(results)
    stats["hot_industries"] = sorted(hot_names)[:10]
    return results[:top_n], stats


def check_ma20_trend_exit(hist: pd.DataFrame) -> Tuple[bool, str]:
    """
    趋势出场：连续 2 日收在 MA5 下，且 MA5 下穿 MA10。
    """
    if hist.empty or len(hist) < 12:
        return False, ""
    close = pd.to_numeric(hist["close"], errors="coerce")
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    if len(close) < 3:
        return False, ""
    below_2d = float(close.iloc[-1]) < float(ma5.iloc[-1]) and float(close.iloc[-2]) < float(ma5.iloc[-2])
    death_cross = float(ma5.iloc[-1]) < float(ma10.iloc[-1]) and float(ma5.iloc[-2]) >= float(ma10.iloc[-2])
    if below_2d and float(ma5.iloc[-1]) < float(ma10.iloc[-1]):
        return True, "连续跌破MA5且5日下穿10日线"
    if death_cross and float(close.iloc[-1]) < float(ma5.iloc[-1]):
        return True, "5日下穿10日线+收破MA5"
    return False, ""
