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
from quantpy.paths import MIDTERM_OUTPUT_DIR
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import (
    ensure_industry_map,
    exclude_bse_from_df,
    get_market_spot,
    get_stock_code_column,
    get_stock_hist,
    get_stock_name_column,
    is_bse_code,
)

OUTPUT_DIR = MIDTERM_OUTPUT_DIR
TRIPLE_VOLUME_RATIO = 3.0
SELECT_WINDOW_START = dt_time(14, 45)
SELECT_WINDOW_END = dt_time(15, 5)
SCAN_WORKERS = 8
PREFILTER_DEFAULT = 800
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


def _evaluate_triple_volume(
    hist: pd.DataFrame,
    *,
    name: str = "",
    spot_volume: Optional[float] = None,
) -> Optional[dict]:
    """判定三倍量 + 一阳穿三线。"""
    if _is_st_or_delist_name(name):
        return None
    if hist is None or len(hist) < 22:
        return None

    hist = hist.sort_values("date").reset_index(drop=True) if "date" in hist.columns else hist.reset_index(drop=True)
    close = pd.to_numeric(hist["close"], errors="coerce")
    if "open" in hist.columns:
        open_px = pd.to_numeric(hist["open"], errors="coerce")
    else:
        open_px = close

    volume = pd.to_numeric(hist.get("volume", 0), errors="coerce").fillna(0)
    if len(volume) < 2:
        return None

    curr_close = float(close.iloc[-1])
    curr_open = float(open_px.iloc[-1])
    prev_close = float(close.iloc[-2])
    yesterday_vol = float(volume.iloc[-2])
    today_vol = float(spot_volume) if spot_volume is not None and spot_volume > 0 else float(volume.iloc[-1])

    if yesterday_vol <= 0 or today_vol <= 0:
        return None
    vol_ratio = today_vol / yesterday_vol
    if vol_ratio < TRIPLE_VOLUME_RATIO:
        return None

    if curr_close <= curr_open:
        return None

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    if pd.isna(ma5.iloc[-1]) or pd.isna(ma10.iloc[-1]) or pd.isna(ma20.iloc[-1]):
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

    pct_chg = 0.0
    if "pct_chg" in hist.columns and pd.notna(hist["pct_chg"].iloc[-1]):
        pct_chg = float(hist["pct_chg"].iloc[-1])
    elif prev_close > 0:
        pct_chg = (curr_close - prev_close) / prev_close * 100

    score = 60.0
    score += min(25.0, max(0.0, (vol_ratio - TRIPLE_VOLUME_RATIO) * 4.0))
    score += min(10.0, max(0.0, pct_chg * 1.5))
    if curr_close > curr_ma5 > curr_ma10 > curr_ma20:
        score += 5.0

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
        "hold_style": "放量突破均线，可中线跟踪",
        "entry_hint": "尾盘确认后关注次日延续性，勿盲目追高",
        "conditions": conditions,
        "condition_labels": [_CONDITION_LABELS[c] for c in conditions],
        "tags": tags,
    }


class TripleVolumeSelector:
    """三倍量中线选股扫描器。"""

    def __init__(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def _score_candidate(
        self,
        code: str,
        name: str,
        spot: Optional[dict] = None,
    ) -> Optional[dict]:
        hist = get_stock_hist(code, days=40, patch_live=True)
        if hist.empty or len(hist) < 22:
            return None

        spot_volume = None
        if spot:
            raw_vol = spot.get("volume")
            if raw_vol is not None:
                try:
                    spot_volume = float(raw_vol)
                except (TypeError, ValueError):
                    spot_volume = None

        tech = _evaluate_triple_volume(hist, name=name, spot_volume=spot_volume)
        if tech is None:
            return None

        spot_pct = float(spot.get("pct", 0)) if spot else tech["pct_chg"]
        if spot_pct == 0 and tech.get("pct_chg"):
            spot_pct = float(tech["pct_chg"])

        reason = (
            f"{' · '.join(tech['tags'])}；"
            f"{tech.get('hold_style', '')}；"
            f"MA5/10/20={tech['ma5']}/{tech['ma10']}/{tech['ma20']}"
        )

        return {
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

    def recommend_stocks(
        self,
        exclude_codes: Optional[List[str]] = None,
        top_n: int = TOP_N_DEFAULT,
        prefilter: int = PREFILTER_DEFAULT,
        show_progress: bool = False,
        max_workers: int = SCAN_WORKERS,
    ) -> Tuple[pd.DataFrame, dict]:
        select_stats: dict = {
            "market_total": 0,
            "prefilter_count": 0,
            "scored_pass": 0,
            "scored_fail": 0,
            "scored_errors": 0,
            "excluded_held": 0,
            "scan_workers": max_workers,
        }
        exclude = {str(c).zfill(6) for c in (exclude_codes or [])}

        _progress("  拉取全市场行情…", show_progress)
        market = get_market_spot(verbose=show_progress, force_refresh=False)
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

        df["_price"] = _spot_price_series(df)
        pool = df[(df["_price"] > 0) & (df["_pct"] > 0)].copy()
        if name_col and name_col in pool.columns:
            pool = pool[~pool[name_col].astype(str).map(_is_st_or_delist_name)]

        if pool.empty:
            _progress("  初筛无阳线非ST标的", show_progress)
            return pd.DataFrame(), select_stats

        pool["_rank"] = (
            pool["_volume"].clip(0, 1e10) / 1e10 * 0.55
            + pool["_turnover"].clip(0, 20) / 20 * 0.25
            + pool["_pct"].clip(0, 10) / 10 * 0.20
        )
        candidates = pool.sort_values("_rank", ascending=False).head(max(prefilter, 200))
        select_stats["prefilter_count"] = len(candidates)
        _progress(
            f"  初筛 {len(candidates)} 只（阳线+非ST，按量能排序），"
            f"多线程评分（{max_workers} 线程）…",
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
                tasks.append({
                    "code": code,
                    "name": name,
                    "spot": {
                        "pct": row["_pct"],
                        "volume": float(row["_volume"]) if pd.notna(row.get("_volume")) else None,
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
                        task["code"], task["name"], spot=task["spot"],
                    )
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                    if show_progress and (done % 50 == 0 or done == total):
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
        f"初筛 {stats.get('prefilter_count', 0)} → "
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
    _progress(f"扫描完成：命中 {len(rec_records)} 只，报告已保存", show_progress)
    return result
