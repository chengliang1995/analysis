"""短线强势股筛选（short-term-stock-picker 本仓库适配）。

策略：近20日涨停基因 + 流通市值≤150亿 + 均线多头 + 换手0.5–10% + 量比评分。
落盘：output/short_term/（独立于 ultra_short / midterm）。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from quantpy.json_util import sanitize_for_json
from quantpy.paths import CACHE_DIR, OUTPUT_DIR
from quantpy.qstock_strategy_optimizer import StrategyOptimizer
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import get_market_spot, get_stock_hist, is_bse_code

STRATEGY_ID = "short_term_picker"
SHORT_TERM_OUTPUT_DIR = OUTPUT_DIR / "short_term"
ZT_CACHE_DIR = CACHE_DIR / "zt_pool"
MARKET_CAP_MAX_YI = 150.0
LIMIT_LOOKBACK = 20
HIST_WORKERS = 8
MAX_ANALYZE = 400  # 技术面分析上限，控制耗时


def _progress(msg: str, show: bool) -> None:
    if show:
        print(msg, flush=True)


def _board_label(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("000", "001", "002", "003")):
        return "深交所"
    return "上交所"


def _allowed_board(code: str) -> bool:
    code = str(code).zfill(6)
    if is_bse_code(code):
        return False
    return code.startswith((
        "600", "601", "603", "605",
        "000", "001", "002", "003",
        "300", "301",
        "688", "689",
    ))


def _cap_to_yi(raw: Any) -> Optional[float]:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0 or pd.isna(v):
        return None
    # 元 → 亿；已是亿则保持
    return v / 1e8 if v > 1e6 else v


def _trade_date_candidates(n: int = 30) -> List[str]:
    """日历回溯工作日（含今天），供涨停池拉取；实际交易日以接口有数据为准。"""
    out: List[str] = []
    d = datetime.now()
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def _fetch_zt_pool_day(date_yyyymmdd: str) -> List[dict]:
    """单日涨停池；失败返回空。结果写 cache。"""
    ZT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = ZT_CACHE_DIR / f"zt_{date_yyyymmdd}.json"
    if cache_path.exists():
        try:
            age_h = (time.time() - cache_path.stat().st_mtime) / 3600
            # 当日池缓存 2h；历史日长期有效
            today = datetime.now().strftime("%Y%m%d")
            if date_yyyymmdd < today or age_h < 2:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                return payload.get("items") or []
        except (OSError, json.JSONDecodeError):
            pass
    try:
        import akshare as ak

        df = ak.stock_zt_pool_em(date=date_yyyymmdd)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    items: List[dict] = []
    for _, row in df.iterrows():
        code = str(row.get("代码") or "").zfill(6)
        name = str(row.get("名称") or "").strip()
        if not code or not name:
            continue
        circ = row.get("流通市值", 0)
        total = row.get("总市值", 0)
        try:
            circ_f = float(circ or 0)
        except (TypeError, ValueError):
            circ_f = 0.0
        try:
            total_f = float(total or 0)
        except (TypeError, ValueError):
            total_f = 0.0
        items.append({
            "code": code,
            "name": name,
            "market_cap": circ_f if circ_f > 0 else total_f,
            "industry": str(row.get("所属行业") or ""),
        })
    try:
        cache_path.write_text(
            json.dumps({"date": date_yyyymmdd, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass
    return items


def collect_limit_up_universe(
    lookback: int = LIMIT_LOOKBACK,
    *,
    show_progress: bool = True,
) -> Dict[str, dict]:
    """近 N 日涨停池聚合；接口失败则返回空（由调用方走 K 线回退）。"""
    dates = _trade_date_candidates(lookback + 10)
    all_data: Dict[str, dict] = {}
    ok_days = 0
    _progress(f"  拉取近 {lookback} 日涨停池…", show_progress)
    for i, date in enumerate(dates):
        if ok_days >= lookback:
            break
        items = _fetch_zt_pool_day(date)
        if not items:
            continue
        ok_days += 1
        for it in items:
            code = it["code"]
            if code not in all_data:
                all_data[code] = {
                    "name": it["name"],
                    "count": 0,
                    "market_cap": it.get("market_cap") or 0,
                    "industry": it.get("industry") or "",
                    "source": "zt_pool",
                }
            all_data[code]["count"] += 1
            if not all_data[code].get("market_cap") and it.get("market_cap"):
                all_data[code]["market_cap"] = it["market_cap"]
        if ok_days % 5 == 0:
            _progress(f"    已取到 {ok_days}/{lookback} 个交易日 · 累计 {len(all_data)} 只", show_progress)
        time.sleep(0.15)
    _progress(f"  涨停池：有效日 {ok_days} · 标的 {len(all_data)}", show_progress)
    return all_data


def _count_limit_ups_from_hist(hist: pd.DataFrame, code: str, lookback: int = LIMIT_LOOKBACK) -> int:
    if hist is None or hist.empty or "close" not in hist.columns:
        return 0
    df = hist.tail(lookback + 2).copy()
    if "pct_chg" in df.columns:
        pct = pd.to_numeric(df["pct_chg"], errors="coerce")
    else:
        close = pd.to_numeric(df["close"], errors="coerce")
        pct = close.pct_change() * 100
    thr = StrategyOptimizer.limit_up_pct_threshold(code) - 0.3
    return int((pct.fillna(-999) >= thr).sum())


def _analyze_one(
    code: str,
    name: str,
    *,
    limit_count: int,
    market_cap_raw: Any,
    industry: str = "",
    spot_row: Optional[dict] = None,
) -> Optional[dict]:
    if "ST" in str(name).upper() or "退" in str(name):
        return None
    if not _allowed_board(code):
        return None

    cap_yi = _cap_to_yi(market_cap_raw)
    if spot_row and (cap_yi is None or cap_yi <= 0):
        cap_yi = _cap_to_yi(spot_row.get("market_cap"))
    if cap_yi is None or cap_yi <= 0 or cap_yi > MARKET_CAP_MAX_YI:
        return None

    try:
        hist = get_stock_hist(code, days=60, patch_live=False)
    except Exception:
        return None
    if hist is None or hist.empty or len(hist) < 25 or "close" not in hist.columns:
        return None

    if limit_count <= 0:
        limit_count = _count_limit_ups_from_hist(hist, code)
    if limit_count < 1:
        return None

    close_s = pd.to_numeric(hist["close"], errors="coerce")
    ma5 = float(close_s.rolling(5).mean().iloc[-1])
    ma10 = float(close_s.rolling(10).mean().iloc[-1])
    ma20 = float(close_s.rolling(20).mean().iloc[-1])
    price = float(close_s.iloc[-1])
    if not (price >= ma5 and ma5 > ma10 > ma20):
        return None

    vol = pd.to_numeric(hist.get("volume", pd.Series(dtype=float)), errors="coerce")
    if len(vol) < 10:
        return None
    vol_last5 = float(vol.tail(5).mean())
    vol_prev5 = float(vol.tail(10).iloc[:-5].mean())
    vol_ratio = vol_last5 / vol_prev5 if vol_prev5 > 0 else 1.0

    # 换手：优先用 K 线 turnover；否则用 spot
    if "turnover" in hist.columns:
        turn = pd.to_numeric(hist["turnover"], errors="coerce").tail(3).mean()
        turnover = float(turn) if pd.notna(turn) else 0.0
        # 部分源为小数
        if turnover <= 1.0:
            turnover *= 100
    else:
        turnover = float(spot_row.get("turnover") or 0) if spot_row else 0.0
    if turnover > 10 or turnover < 0.5:
        return None

    recent3 = close_s.tail(3)
    price_up_3d = float(recent3.iloc[-1]) > float(recent3.iloc[0])

    tech = 30
    if vol_ratio >= 1.5:
        tech += 15
    elif vol_ratio >= 1.2:
        tech += 10
    else:
        tech += 5
    if price_up_3d and vol_ratio >= 1.2:
        tech += 10
    if 2 <= turnover <= 8:
        tech += 5

    total = limit_count * 20 + tech + vol_ratio * 5
    return {
        "code": code,
        "name": name,
        "strategy_id": STRATEGY_ID,
        "limit_up_count_20d": int(limit_count),
        "market_cap_yi": round(cap_yi, 2),
        "price": round(price, 2),
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "turnover": round(turnover, 2),
        "vol_ratio_5d": round(vol_ratio, 2),
        "price_up_3d": bool(price_up_3d),
        "tech_score": tech,
        "score": round(total, 1),
        "board": _board_label(code),
        "industry": industry or (spot_row or {}).get("industry") or "",
    }


def _spot_index(spot: pd.DataFrame) -> Dict[str, dict]:
    if spot is None or spot.empty or "code" not in spot.columns:
        return {}
    out: Dict[str, dict] = {}
    for _, row in spot.iterrows():
        code = str(row["code"]).zfill(6)
        out[code] = row.to_dict()
    return out


def _fallback_universe_from_spot(
    spot: pd.DataFrame,
    *,
    show_progress: bool = True,
) -> Dict[str, dict]:
    """涨停池不可用时：市值≤150亿的非 ST 池，涨停次数由 K 线回推。"""
    _progress("  涨停池不可用，改用行情快照候选（K 线回推涨停）…", show_progress)
    if spot is None or spot.empty:
        return {}
    df = spot.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    name_col = "name" if "name" in df.columns else None
    universe: Dict[str, dict] = {}
    for _, row in df.iterrows():
        code = str(row["code"]).zfill(6)
        name = str(row[name_col] if name_col else code)
        if "ST" in name.upper() or "退" in name:
            continue
        if not _allowed_board(code):
            continue
        cap_yi = _cap_to_yi(row.get("market_cap"))
        if cap_yi is None or cap_yi > MARKET_CAP_MAX_YI:
            continue
        turn = float(row.get("turnover") or 0)
        if turn and (turn > 10 or turn < 0.5):
            continue
        universe[code] = {
            "name": name,
            "count": 0,  # 分析时用 hist 回推
            "market_cap": row.get("market_cap") or 0,
            "industry": str(row.get("industry") or ""),
            "source": "spot_fallback",
        }
        if len(universe) >= MAX_ANALYZE:
            break
    _progress(f"  快照候选 {len(universe)} 只", show_progress)
    return universe


def run_short_term_pick(
    *,
    max_candidates: int = 50,
    max_analyze: int = MAX_ANALYZE,
    show_progress: bool = True,
) -> dict:
    """执行短线强势筛选并落盘。"""
    _progress("=" * 50, show_progress)
    _progress("短线强势股筛选 · short_term_picker", show_progress)

    try:
        spot = get_market_spot(verbose=False)
    except Exception:
        spot = pd.DataFrame()
    spot_map = _spot_index(spot)

    universe = collect_limit_up_universe(LIMIT_LOOKBACK, show_progress=show_progress)
    source = "zt_pool"
    if len(universe) < 10:
        universe = _fallback_universe_from_spot(spot, show_progress=show_progress)
        source = "spot_fallback"

    # 先按市值粗筛，再截断分析量
    items: List[Tuple[str, dict]] = []
    for code, info in universe.items():
        cap_yi = _cap_to_yi(info.get("market_cap"))
        if cap_yi is None and code in spot_map:
            cap_yi = _cap_to_yi(spot_map[code].get("market_cap"))
        if cap_yi is not None and cap_yi > MARKET_CAP_MAX_YI:
            continue
        if not _allowed_board(code):
            continue
        items.append((code, info))

    # 涨停次数多的优先分析
    items.sort(key=lambda x: int(x[1].get("count") or 0), reverse=True)
    items = items[: max(1, max_analyze)]

    _progress(f"  技术面分析 {len(items)} 只…", show_progress)
    results: List[dict] = []

    def _job(pair: Tuple[str, dict]) -> Optional[dict]:
        code, info = pair
        return _analyze_one(
            code,
            str(info.get("name") or code),
            limit_count=int(info.get("count") or 0),
            market_cap_raw=info.get("market_cap"),
            industry=str(info.get("industry") or ""),
            spot_row=spot_map.get(code),
        )

    with ThreadPoolExecutor(max_workers=HIST_WORKERS) as pool:
        futs = {pool.submit(_job, it): it[0] for it in items}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                results.append(row)
            if show_progress and done % 40 == 0:
                _progress(f"    进度 {done}/{len(items)} · 命中 {len(results)}", show_progress)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = results[: max(1, max_candidates)]

    result = {
        "ok": True,
        "strategy_id": STRATEGY_ID,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "stats": {
            "universe": len(universe),
            "analyzed": len(items),
            "passed": len(results),
            "returned": len(top),
            "market_cap_max_yi": MARKET_CAP_MAX_YI,
            "limit_lookback": LIMIT_LOOKBACK,
        },
        "candidates": top,
        "notes": [
            "硬筛：市值≤150亿、近20日涨停≥1、MA多头、换手0.5–10%。",
            "独立于 ultra_short / midterm 落盘。",
        ],
    }
    result["markdown"] = format_short_term_markdown(result)
    path = _save_result(result)
    result["artifact"] = str(path)
    if show_progress:
        print(result["markdown"])
    return result


def format_short_term_markdown(payload: dict) -> str:
    stats = payload.get("stats") or {}
    lines = [
        "# 短线强势股筛选",
        "",
        f"- strategy: `{STRATEGY_ID}`",
        f"- 生成: {payload.get('generated_at')}",
        f"- 来源: {payload.get('source')}",
        f"- 宇宙/分析/通过: {stats.get('universe')}/{stats.get('analyzed')}/{stats.get('passed')}",
        "",
        "## 候选（按综合分）",
        "",
    ]
    cands = payload.get("candidates") or []
    if not cands:
        lines.append("（无命中）")
    else:
        rows = [
            [
                c.get("code"),
                truncate_display(c.get("name"), 8),
                c.get("limit_up_count_20d"),
                c.get("market_cap_yi"),
                c.get("price"),
                c.get("turnover"),
                c.get("vol_ratio_5d"),
                c.get("score"),
                c.get("board"),
            ]
            for c in cands
        ]
        lines.append(
            format_markdown_table(
                ["代码", "名称", "涨停", "市值亿", "现价", "换手%", "量比", "评分", "板块"],
                rows,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _save_result(result: dict) -> Path:
    SHORT_TERM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = sanitize_for_json(result)
    path = SHORT_TERM_OUTPUT_DIR / f"short_term_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (SHORT_TERM_OUTPUT_DIR / "short_term_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    md = result.get("markdown") or ""
    (SHORT_TERM_OUTPUT_DIR / f"short_term_{stamp}.md").write_text(md, encoding="utf-8")
    (SHORT_TERM_OUTPUT_DIR / "short_term_latest.md").write_text(md, encoding="utf-8")
    cands = result.get("candidates") or []
    if cands:
        pd.DataFrame(cands).to_csv(
            SHORT_TERM_OUTPUT_DIR / f"short_term_{stamp}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        pd.DataFrame(cands).to_csv(
            SHORT_TERM_OUTPUT_DIR / "short_term_latest.csv",
            index=False,
            encoding="utf-8-sig",
        )
    return path


def load_latest_short_term() -> dict:
    path = SHORT_TERM_OUTPUT_DIR / "short_term_latest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
