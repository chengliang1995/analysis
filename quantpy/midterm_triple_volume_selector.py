"""
中线选股策略：三倍量 + 一阳穿三线
- 尾盘 14:45 起筛选
- 当日成交量 ≥ 昨日 3 倍
- 非 ST
- 当日阳线，收盘价上穿 MA5 / MA10 / MA20
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time
from typing import List, Optional, Tuple

import pandas as pd

from quantpy.json_util import df_to_records_safe, json_safe_float, sanitize_for_json
from quantpy.paths import CACHE_DIR, MIDTERM_OUTPUT_DIR
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import (
    ensure_industry_map,
    exclude_bse_from_df,
    get_market_spot,
    get_stock_code_column,
    get_stock_hist,
    get_stock_name_column,
    is_bse_code,
    load_daily_close_snapshot,
)

OUTPUT_DIR = MIDTERM_OUTPUT_DIR
DAILY_CLOSE_DIR = CACHE_DIR / "daily_close"
TRIPLE_VOLUME_RATIO = 3.0
# 跟进统计：当日涨 7–9% 三倍量胜率约 44%；≥11% 档均值转负 → 硬过滤
TRIPLE_PCT_WEAK_LOW = 7.0
TRIPLE_PCT_WEAK_HIGH = 9.0
TRIPLE_PCT_EXTREME = 11.0
# 量比软预筛（同单位手/手），再拉 K 线做硬筛 3 倍 + 一阳穿三线
VOL_PREFILTER_RATIO = 2.5
SELECT_WINDOW_START = dt_time(14, 45)
SELECT_WINDOW_END = dt_time(15, 5)
SCAN_WORKERS = 16
PREFILTER_DEFAULT = 800
# 量比预筛失败时的回退上限；有昨收快照时通常远小于此
MAX_HIST_SCAN = 250
HIST_DAYS = 45
TOP_N_DEFAULT = 30

TRIPLE_VOLUME_SELECT_CONDITIONS = [
    {"id": "non_st", "label": "非ST", "category": "基本面"},
    {"id": "vol_3x", "label": "成交量≥昨日3倍", "category": "量能"},
    {"id": "yang_line", "label": "当日阳线", "category": "技术面"},
    {"id": "cross_ma5", "label": "上穿MA5", "category": "技术面"},
    {"id": "cross_ma10", "label": "上穿MA10", "category": "技术面"},
    {"id": "cross_ma20", "label": "上穿MA20", "category": "技术面"},
]

_CONDITION_LABELS = {c["id"]: c["label"] for c in TRIPLE_VOLUME_SELECT_CONDITIONS}


def get_triple_volume_select_conditions() -> List[dict]:
    return list(TRIPLE_VOLUME_SELECT_CONDITIONS)


def is_triple_pct_chase_risky(pct: float) -> bool:
    """三倍量当日涨幅风险区：7–9% 跟进偏弱，≥11% 易冲高回落。"""
    if pct <= 0:
        return False
    if TRIPLE_PCT_WEAK_LOW <= pct < TRIPLE_PCT_WEAK_HIGH:
        return True
    return pct >= TRIPLE_PCT_EXTREME


def _is_st_or_delist_name(name: str) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "退" in str(name or "")


def _progress(msg: str, show: bool = True) -> None:
    if show:
        print(msg, flush=True)


def _spot_price_series(df: pd.DataFrame) -> pd.Series:
    for col in ("price", "close", "最新价"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(index=df.index, dtype=float)


def _is_select_window(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    t = now.time()
    return SELECT_WINDOW_START <= t <= SELECT_WINDOW_END


def _align_volume_unit(today_vol: float, ref_vol: float) -> float:
    """将 today_vol 对齐到与 ref_vol 相同单位（腾讯实时=手，K线=股）。"""
    if today_vol <= 0 or ref_vol <= 0:
        return today_vol
    ratio = today_vol / ref_vol
    if ratio < 0.02:
        return today_vol * 100
    if ratio > 50:
        return today_vol / 100
    return today_vol


def _hist_date_str(value) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text


def _load_prev_day_volume_map(today: Optional[str] = None) -> Tuple[dict, str]:
    """读取上一交易日收盘快照成交量（与实时行情同为「手」）。"""
    today = today or datetime.now().strftime("%Y-%m-%d")
    files = sorted(
        p for p in DAILY_CLOSE_DIR.glob("*.csv")
        if len(p.stem) == 10 and p.stem < today
    )
    if not files:
        return {}, ""
    prev_date = files[-1].stem
    snap = load_daily_close_snapshot(prev_date, allow_partial=True)
    if snap.empty or "volume" not in snap.columns:
        return {}, prev_date
    vol = pd.to_numeric(snap["volume"], errors="coerce")
    codes = snap["code"].astype(str).str.zfill(6)
    mapping = {
        code: float(v)
        for code, v in zip(codes, vol)
        if pd.notna(v) and float(v) > 0
    }
    return mapping, prev_date


def _evaluate_triple_volume(
    hist: pd.DataFrame,
    *,
    name: str = "",
    spot_volume: Optional[float] = None,
    spot_price: Optional[float] = None,
    spot_open: Optional[float] = None,
    spot_pct: Optional[float] = None,
    prev_day_volume: Optional[float] = None,
) -> Optional[dict]:
    """判定三倍量 + 一阳穿三线。优先用实时价/量，K 线只算均线与昨日基准。"""
    if _is_st_or_delist_name(name):
        return None
    if hist is None or len(hist) < 21:
        return None

    hist = hist.sort_values("date").reset_index(drop=True) if "date" in hist.columns else hist.reset_index(drop=True)
    today = datetime.now().strftime("%Y-%m-%d")
    last_date = _hist_date_str(hist["date"].iloc[-1]) if "date" in hist.columns else ""
    # 盘中 K 线末根可能是不完整今日，统一剥掉后用实时价拼接
    base = hist.iloc[:-1].copy() if last_date == today and len(hist) > 21 else hist
    if len(base) < 21:
        return None

    base_close = pd.to_numeric(base["close"], errors="coerce")
    base_vol = pd.to_numeric(base.get("volume", 0), errors="coerce").fillna(0)
    prev_close = float(base_close.iloc[-1])
    if prev_close <= 0:
        return None

    curr_close = float(spot_price) if spot_price and spot_price > 0 else float(base_close.iloc[-1])
    if spot_open is not None and spot_open > 0:
        curr_open = float(spot_open)
    elif last_date == today and "open" in hist.columns:
        curr_open = float(pd.to_numeric(hist["open"], errors="coerce").iloc[-1])
    else:
        curr_open = curr_close

    if curr_close <= curr_open:
        return None

    # 昨量：优先昨收快照（手），否则 K 线末根并对齐单位
    yesterday_vol_raw = (
        float(prev_day_volume)
        if prev_day_volume is not None and prev_day_volume > 0
        else float(base_vol.iloc[-1])
    )
    today_vol_raw = (
        float(spot_volume) if spot_volume is not None and spot_volume > 0
        else float(pd.to_numeric(hist.get("volume", 0), errors="coerce").fillna(0).iloc[-1])
    )
    if yesterday_vol_raw <= 0 or today_vol_raw <= 0:
        return None

    # 快照对快照（同单位）无需对齐；快照对 K 线才对齐
    if prev_day_volume is not None and prev_day_volume > 0 and spot_volume is not None and spot_volume > 0:
        yesterday_vol = yesterday_vol_raw
        today_vol = today_vol_raw
    else:
        yesterday_vol = yesterday_vol_raw
        today_vol = _align_volume_unit(today_vol_raw, yesterday_vol)

    vol_ratio = today_vol / yesterday_vol
    if vol_ratio < TRIPLE_VOLUME_RATIO:
        return None

    closes = pd.concat([base_close, pd.Series([curr_close])], ignore_index=True)
    ma5 = closes.rolling(5).mean()
    ma10 = closes.rolling(10).mean()
    ma20 = closes.rolling(20).mean()
    if pd.isna(ma5.iloc[-1]) or pd.isna(ma10.iloc[-1]) or pd.isna(ma20.iloc[-1]):
        return None
    if pd.isna(ma5.iloc[-2]) or pd.isna(ma10.iloc[-2]) or pd.isna(ma20.iloc[-2]):
        return None

    curr_ma5 = float(ma5.iloc[-1])
    curr_ma10 = float(ma10.iloc[-1])
    curr_ma20 = float(ma20.iloc[-1])
    prev_ma5 = float(ma5.iloc[-2])
    prev_ma10 = float(ma10.iloc[-2])
    prev_ma20 = float(ma20.iloc[-2])

    cross5 = prev_close <= prev_ma5 and curr_close > curr_ma5
    cross10 = prev_close <= prev_ma10 and curr_close > curr_ma10
    cross20 = prev_close <= prev_ma20 and curr_close > curr_ma20
    if not (cross5 and cross10 and cross20):
        return None

    if spot_pct is not None:
        pct_chg = float(spot_pct)
    elif prev_close > 0:
        pct_chg = (curr_close - prev_close) / prev_close * 100
    else:
        pct_chg = 0.0

    if is_triple_pct_chase_risky(pct_chg):
        return None

    score = 60.0
    score += min(25.0, max(0.0, (vol_ratio - TRIPLE_VOLUME_RATIO) * 4.0))
    # 涨幅温和加分；5–7% 略减分；7–9% 与 ≥11% 已在上方硬过滤
    if 0 < pct_chg < 5:
        score += min(6.0, pct_chg * 1.2)
    elif pct_chg >= 5:
        score -= 2.0
    if curr_close > curr_ma5 > curr_ma10 > curr_ma20:
        score += 5.0
    if vol_ratio >= 5:
        score += 3.0

    conditions = ["non_st", "vol_3x", "yang_line", "cross_ma5", "cross_ma10", "cross_ma20"]
    tags = [
        f"量{vol_ratio:.1f}倍",
        "一阳穿三线",
        f"涨{pct_chg:+.1f}%",
    ]

    return {
        "price": round(curr_close, 2),
        "pct_chg": round(pct_chg, 2),
        "volume_ratio": round(vol_ratio, 2),
        "today_volume": int(today_vol),
        "yesterday_volume": int(yesterday_vol),
        "ma5": round(curr_ma5, 2),
        "ma10": round(curr_ma10, 2),
        "ma20": round(curr_ma20, 2),
        "midterm_score": round(min(score, 99.0), 1),
        "trend": "三倍量突破",
        "hold_style": "突破日仅入观察池，5日内缩量站稳MA5再买",
        "entry_hint": "勿在突破日上穿当天追价，等缩量至突破前并站稳5日线",
        "conditions": conditions,
        "condition_labels": [_CONDITION_LABELS[c] for c in conditions],
        "tags": tags,
    }


class TripleVolumeSelector:
    """三倍量中线选股扫描器。"""

    def __init__(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._tuning = None

    def _score_candidate(
        self,
        code: str,
        name: str,
        spot: Optional[dict] = None,
        prev_day_volume: Optional[float] = None,
    ) -> Optional[dict]:
        # 不 patch_live：避免每只票再打一次实时行情；量价用 spot / 昨收快照
        hist = get_stock_hist(code, days=HIST_DAYS, patch_live=False)
        if hist.empty or len(hist) < 21:
            return None

        spot = spot or {}
        spot_volume = None
        raw_vol = spot.get("volume")
        if raw_vol is not None:
            try:
                spot_volume = float(raw_vol)
            except (TypeError, ValueError):
                spot_volume = None

        spot_price = None
        raw_px = spot.get("price")
        if raw_px is not None:
            try:
                spot_price = float(raw_px)
            except (TypeError, ValueError):
                spot_price = None

        spot_open = None
        raw_open = spot.get("open")
        if raw_open is not None:
            try:
                spot_open = float(raw_open)
            except (TypeError, ValueError):
                spot_open = None

        spot_pct = float(spot.get("pct", 0) or 0)

        tech = _evaluate_triple_volume(
            hist,
            name=name,
            spot_volume=spot_volume,
            spot_price=spot_price,
            spot_open=spot_open,
            spot_pct=spot_pct if spot_pct else None,
            prev_day_volume=prev_day_volume,
        )
        if tech is None:
            return None

        if spot_pct == 0 and tech.get("pct_chg"):
            spot_pct = float(tech["pct_chg"])

        reason = (
            f"{' · '.join(tech['tags'])}；"
            f"{tech.get('hold_style', '')}；"
            f"MA5/10/20={tech['ma5']}/{tech['ma10']}/{tech['ma20']}"
        )

        item = {
            "code": code,
            "name": name,
            "price": tech["price"],
            "pct_chg": json_safe_float(spot_pct, digits=2),
            "volume_ratio": tech["volume_ratio"],
            "today_volume": tech["today_volume"],
            "yesterday_volume": tech["yesterday_volume"],
            "midterm_score": tech["midterm_score"],
            "trend": tech["trend"],
            "ma5": tech["ma5"],
            "ma10": tech["ma10"],
            "ma20": tech["ma20"],
            "hold_style": tech.get("hold_style", ""),
            "entry_hint": tech.get("entry_hint", ""),
            "tags": ",".join(tech["tags"]),
            "conditions": tech["conditions"],
            "condition_labels": tech["condition_labels"],
            "reason": reason,
        }
        if self._tuning is not None:
            from quantpy.selection_tuning import apply_triple_tuning

            item = apply_triple_tuning(item, self._tuning)
            if item is None:
                return None
        return item

    def recommend_stocks(
        self,
        exclude_codes: Optional[List[str]] = None,
        top_n: int = TOP_N_DEFAULT,
        prefilter: int = PREFILTER_DEFAULT,
        show_progress: bool = False,
        max_workers: int = SCAN_WORKERS,
        tuning=None,
    ) -> Tuple[pd.DataFrame, dict]:
        from quantpy.selection_tuning import build_selection_tuning, format_tuning_summary

        self._tuning = tuning if tuning is not None else build_selection_tuning(for_sim=False)
        if show_progress and self._tuning:
            _progress(format_tuning_summary(self._tuning), show_progress)
        select_stats: dict = {
            "market_total": 0,
            "prefilter_count": 0,
            "vol_prefilter_count": 0,
            "hist_scan_count": 0,
            "scored_pass": 0,
            "scored_fail": 0,
            "scored_errors": 0,
            "excluded_held": 0,
            "scan_workers": max_workers,
            "prev_trade_date": "",
            "triple_min_score": int(getattr(self._tuning, "triple_min_score", 60) or 60),
        }
        exclude = {str(c).zfill(6) for c in (exclude_codes or [])}

        prev_vol_map, prev_date = _load_prev_day_volume_map()
        select_stats["prev_trade_date"] = prev_date

        today = datetime.now().strftime("%Y-%m-%d")
        snap_path = DAILY_CLOSE_DIR / f"{today}.csv"
        force_refresh = True
        if snap_path.exists():
            age_min = (datetime.now().timestamp() - snap_path.stat().st_mtime) / 60
            if age_min <= 3:
                force_refresh = False
                _progress(f"  今日快照较新（{age_min:.1f} 分钟前），跳过全市场重拉", show_progress)

        _progress("  拉取全市场行情（实时成交量）…", show_progress)
        market = get_market_spot(verbose=show_progress, force_refresh=force_refresh)
        if market.empty:
            _progress("  行情为空，跳过扫描", show_progress)
            return pd.DataFrame(), select_stats

        select_stats["market_total"] = len(market)
        code_col = get_stock_code_column(market)
        name_col = get_stock_name_column(market)
        df = exclude_bse_from_df(market.copy(), code_col)
        if df.empty:
            return pd.DataFrame(), select_stats

        pct_col = next((c for c in ("pct_chg", "changepercent", "涨跌幅") if c in df.columns), None)
        turnover_col = next((c for c in ("turnover", "turnoverratio", "换手率") if c in df.columns), None)
        volume_col = next((c for c in ("volume", "成交量") if c in df.columns), None)
        open_col = next((c for c in ("open", "开盘价") if c in df.columns), None)

        if pct_col:
            df["_pct"] = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
        else:
            df["_pct"] = 0
        if turnover_col:
            df["_turnover"] = pd.to_numeric(df[turnover_col], errors="coerce").fillna(0)
        else:
            df["_turnover"] = 0
        if volume_col:
            df["_volume"] = pd.to_numeric(df[volume_col], errors="coerce").fillna(0)
        else:
            df["_volume"] = 0.0
        if open_col:
            df["_open"] = pd.to_numeric(df[open_col], errors="coerce")
        else:
            df["_open"] = float("nan")

        df["_price"] = _spot_price_series(df)
        # 阳线：优先 收盘>开盘；无开盘价时用涨幅>0
        yang = (df["_price"] > 0) & (
            (df["_open"].notna() & (df["_price"] > df["_open"]))
            | (df["_open"].isna() & (df["_pct"] > 0))
        )
        pool = df[yang].copy()
        if name_col and name_col in pool.columns:
            pool = pool[~pool[name_col].astype(str).map(_is_st_or_delist_name)]

        if pool.empty:
            _progress("  初筛无阳线非ST标的", show_progress)
            return pd.DataFrame(), select_stats

        prev_vol_map, prev_date = _load_prev_day_volume_map()
        select_stats["prev_trade_date"] = prev_date
        if prev_vol_map:
            codes = pool[code_col].astype(str).str.zfill(6)
            pool["_prev_vol"] = codes.map(prev_vol_map)
            pool["_vol_ratio"] = pool["_volume"] / pool["_prev_vol"].replace(0, pd.NA)
            vol_hit = pool[pool["_vol_ratio"] >= VOL_PREFILTER_RATIO].copy()
            select_stats["vol_prefilter_count"] = len(vol_hit)
            _progress(
                f"  量比预筛（昨收 {prev_date}）：{len(pool)} 阳线 → "
                f"{len(vol_hit)} 只 ≥{VOL_PREFILTER_RATIO}x",
                show_progress,
            )
            if not vol_hit.empty:
                candidates = vol_hit.sort_values("_vol_ratio", ascending=False).head(MAX_HIST_SCAN)
            else:
                candidates = pool.sort_values("_volume", ascending=False).head(min(prefilter, 200))
        else:
            _progress("  无昨收成交量快照，回退为量能排序初筛", show_progress)
            pool["_rank"] = (
                pool["_volume"].clip(0, 1e10) / 1e10 * 0.55
                + pool["_turnover"].clip(0, 20) / 20 * 0.25
                + pool["_pct"].clip(0, 10) / 10 * 0.20
            )
            candidates = pool.sort_values("_rank", ascending=False).head(min(prefilter, MAX_HIST_SCAN))

        select_stats["prefilter_count"] = len(candidates)
        select_stats["hist_scan_count"] = len(candidates)
        _progress(
            f"  拉取 K 线评分 {len(candidates)} 只（{max_workers} 线程，无逐票实时补丁）…",
            show_progress,
        )

        def _scan_rows(rows: pd.DataFrame) -> Tuple[List[dict], int, int, int]:
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
                prev_v = row.get("_prev_vol")
                try:
                    prev_v_f = float(prev_v) if pd.notna(prev_v) else None
                except (TypeError, ValueError):
                    prev_v_f = None
                open_v = row.get("_open")
                try:
                    open_f = float(open_v) if pd.notna(open_v) else None
                except (TypeError, ValueError):
                    open_f = None
                tasks.append({
                    "code": code,
                    "name": name,
                    "prev_day_volume": prev_v_f,
                    "spot": {
                        "pct": float(row["_pct"]) if pd.notna(row["_pct"]) else 0.0,
                        "volume": float(row["_volume"]) if pd.notna(row.get("_volume")) else None,
                        "price": float(row["_price"]) if pd.notna(row.get("_price")) else None,
                        "open": open_f,
                    },
                })

            results: List[dict] = []
            fail = 0
            errors = 0
            lock = threading.Lock()
            done = 0
            total = len(tasks)

            def _worker(task: dict) -> Optional[dict]:
                try:
                    return self._score_candidate(
                        task["code"],
                        task["name"],
                        spot=task["spot"],
                        prev_day_volume=task.get("prev_day_volume"),
                    )
                except Exception:
                    return None

            workers = max(1, min(max_workers, max(total, 1)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_worker, t): t for t in tasks}
                for fut in as_completed(futures):
                    done += 1
                    try:
                        item = fut.result()
                        if item:
                            with lock:
                                results.append(item)
                        else:
                            fail += 1
                    except Exception:
                        errors += 1
                    if show_progress and (done % 25 == 0 or done == total):
                        _progress(
                            f"  进度 {done}/{total}，命中 {len(results)} 只",
                            show_progress,
                        )

            return results, excluded, fail, errors

        results, excluded, fail, errors = _scan_rows(candidates)
        select_stats["excluded_held"] = excluded
        select_stats["scored_pass"] = len(results)
        select_stats["scored_fail"] = fail
        select_stats["scored_errors"] = errors

        if not results:
            _progress("  无符合条件的三倍量标的", show_progress)
            return pd.DataFrame(), select_stats

        results.sort(key=lambda x: (x["midterm_score"], x["volume_ratio"]), reverse=True)
        enrich_codes = [r["code"] for r in results[: max(top_n * 2, 40)]]
        industry_map = ensure_industry_map(enrich_codes, verbose=show_progress)
        for item in results:
            item["industry"] = industry_map.get(item["code"], "")

        out = pd.DataFrame(results).head(top_n)
        _progress(f"  命中 {len(out)} 只三倍量突破", show_progress)
        return out.reset_index(drop=True), select_stats


def format_triple_volume_report_markdown(result: dict) -> str:
    parts = [
        f"# 三倍量选股报告\n",
        f"生成时间: {result.get('generated_at', '')}\n",
        f"策略: {result.get('style', '三倍量')}\n",
        f"筛选窗口: {result.get('window', '14:45-15:05')}\n\n",
    ]
    stats = result.get("select_stats") or {}
    parts.append(
        f"全市场 {stats.get('market_total', 0)} 只 → "
        f"量比预筛 {stats.get('vol_prefilter_count', stats.get('prefilter_count', 0))} → "
        f"K线 {stats.get('hist_scan_count', stats.get('prefilter_count', 0))} → "
        f"命中 {stats.get('scored_pass', 0)} 只\n\n"
    )

    conds = result.get("select_conditions") or TRIPLE_VOLUME_SELECT_CONDITIONS
    parts.append("## 选股条件\n")
    parts.append(" · ".join(c["label"] for c in conds) + "\n\n")

    recs = result.get("recommendations") or []
    parts.append(f"## 推荐标的 ({len(recs)})\n\n")
    if not recs:
        parts.append("（今日无符合条件标的）\n")
    else:
        rows = [
            [
                r["code"],
                r["name"],
                r.get("industry") or "—",
                f"{r.get('price', 0):.2f}",
                f"{r.get('volume_ratio', 0):.1f}x",
                r["midterm_score"],
                f"{r.get('pct_chg', 0):+.2f}",
                truncate_display(r.get("reason", ""), 32),
            ]
            for r in recs
        ]
        parts.append(
            format_markdown_table(
                ["代码", "名称", "行业", "股价", "量比", "评分", "涨幅%", "理由"],
                rows,
                aligns=["left", "left", "left", "right", "right", "right", "right", "left"],
            )
        )
        parts.append("\n")

    buy_alerts = (
        result.get("buy_alerts")
        or (result.get("watchlist") or {}).get("recent_alerts")
        or (result.get("watchlist") or {}).get("buy_signals")
        or []
    )
    if buy_alerts:
        parts.append(f"\n## 观察池买入提示 ({len(buy_alerts)})\n\n")
        alert_rows = [
            [
                a.get("code", ""),
                a.get("name", ""),
                a.get("pick_date", "") or a.get("signal_date", ""),
                (
                    f"{a.get('buy_signal_price') or a.get('price', 0):.2f}"
                    if (a.get("buy_signal_price") or a.get("price"))
                    else "—"
                ),
                truncate_display(a.get("buy_reason") or a.get("reason", ""), 36),
            ]
            for a in buy_alerts[:10]
        ]
        parts.append(
            format_markdown_table(
                ["代码", "名称", "选股日", "现价", "买入理由"],
                alert_rows,
                aligns=["left", "left", "left", "right", "left"],
            )
        )
        parts.append("\n")

    parts.append("---\n*仅供参考，不构成投资建议。*\n")
    return "".join(parts)


def load_latest_triple_volume_advice() -> dict:
    files = sorted(OUTPUT_DIR.glob("triple_volume_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return sanitize_for_json(json.loads(files[0].read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}


def run_triple_volume_select(
    exclude_codes: Optional[List[str]] = None,
    show_progress: bool = True,
    force: bool = False,
    top_n: int = TOP_N_DEFAULT,
    prefilter: int = PREFILTER_DEFAULT,
) -> dict:
    """执行三倍量选股并保存报告。"""
    now = datetime.now()
    in_window = _is_select_window(now)

    _progress("=" * 50, show_progress)
    _progress("三倍量选股扫描", show_progress)
    if not in_window and not force:
        _progress(
            f"当前非筛选窗口（{SELECT_WINDOW_START.strftime('%H:%M')}-"
            f"{SELECT_WINDOW_END.strftime('%H:%M')}），使用 --force 强制执行",
            show_progress,
        )
    elif in_window:
        _progress(
            f"筛选窗口内（{now.strftime('%H:%M')}）",
            show_progress,
        )

    selector = TripleVolumeSelector()
    recommendations, select_stats = selector.recommend_stocks(
        exclude_codes=exclude_codes,
        top_n=top_n,
        prefilter=prefilter,
        show_progress=show_progress,
    )
    rec_records = df_to_records_safe(recommendations)

    result = {
        "generated_at": now.isoformat(),
        "style": "三倍量",
        "strategy": "triple_volume",
        "window": f"{SELECT_WINDOW_START.strftime('%H:%M')}-{SELECT_WINDOW_END.strftime('%H:%M')}",
        "in_window": in_window,
        "select_stats": select_stats,
        "select_conditions": get_triple_volume_select_conditions(),
        "recommendations": rec_records,
    }

    path = OUTPUT_DIR / f"triple_volume_{now.strftime('%Y%m%d')}.json"
    path.write_text(
        json.dumps(sanitize_for_json(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = format_triple_volume_report_markdown(result)
    md_path = OUTPUT_DIR / f"triple_volume_{now.strftime('%Y%m%d')}.md"
    md_path.write_text(md, encoding="utf-8")
    result["markdown"] = md
    result["report_path"] = str(md_path)

    if rec_records:
        try:
            from quantpy.triple_volume_watchlist import run_watchlist_cycle

            watchlist = run_watchlist_cycle(
                rec_records,
                pick_date=now.strftime("%Y-%m-%d"),
                show_progress=show_progress,
            )
            result["watchlist"] = watchlist
        except Exception as exc:
            if show_progress:
                _progress(f"  观察池记录跳过: {exc}", show_progress)
    else:
        # 当日无命中时仍同步历史选股报告并刷新观察池评估
        try:
            from quantpy.triple_volume_watchlist import run_watchlist_cycle

            result["watchlist"] = run_watchlist_cycle(show_progress=show_progress)
        except Exception as exc:
            if show_progress:
                _progress(f"  观察池评估跳过: {exc}", show_progress)

    _progress(f"扫描完成：命中 {len(rec_records)} 只，报告已保存", show_progress)
    return result
