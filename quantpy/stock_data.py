"""
统一股票数据获取层
支持新浪 / 腾讯 / qstock / AKShare / 东方财富 / baostock 多数据源自动降级
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

import pandas as pd
import requests

T = TypeVar("T")

from quantpy.paths import CACHE_DIR

STOCK_LIST_CACHE = CACHE_DIR / "stock_list.csv"
STOCK_LIST_META = CACHE_DIR / "stock_list_meta.json"
STOCK_FUNDAMENTALS_CACHE = CACHE_DIR / "stock_fundamentals.csv"
STOCK_FUNDAMENTALS_META = CACHE_DIR / "stock_fundamentals_meta.json"
STOCK_INDUSTRY_CACHE = CACHE_DIR / "stock_industry.json"
BOARD_LIST_CACHE = {
    "concept": CACHE_DIR / "board_list_concept.json",
    "industry": CACHE_DIR / "board_list_industry.json",
}
BOARD_CONS_CACHE_DIR = CACHE_DIR / "board_constituents"
BOARD_CACHE_MAX_AGE_MINUTES = 30
DAILY_CLOSE_DIR = CACHE_DIR / "daily_close"
CACHE_MAX_AGE_HOURS = 24
TENCENT_QT_BATCH = 60

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# qstock 历史行情（可选）
_qs_web_data: Optional[Callable] = None
_qs_get_code: Optional[Callable] = None
_qs_available = False

try:
    from qstock.data.trade import get_code as _qs_get_code_func
    from qstock.data.trade import web_data as _qs_web_data_func

    _qs_get_code = _qs_get_code_func
    _qs_web_data = _qs_web_data_func
    _qs_available = True
except Exception:
    pass

COLUMN_ALIASES = {
    "code": ["代码", "股票代码", "symbol", "code"],
    "name": ["名称", "股票名称", "name"],
    "pe": ["市盈率-动态", "市盈率", "pe", "PE", "per"],
    "pb": ["市净率", "pb", "PB"],
    "market_cap": ["总市值", "流通市值", "market_cap", "市值", "mktcap"],
    "price": ["最新价", "现价", "price", "收盘", "trade"],
    "turnover": ["换手率", "turnover", "turnoverratio"],
    "roe": ["roe", "ROE", "净资产收益率"],
    "industry": ["所属行业", "行业", "industry"],
    "profit_yoy": ["净利润同比", "净利同比", "profit_yoy"],
}

# 网络全部失败时的最小兜底列表（常见蓝筹 + 热门股）
OFFLINE_FALLBACK_STOCKS = [
    ("000001", "平安银行"), ("000002", "万科A"), ("000063", "中兴通讯"),
    ("000069", "华侨城A"), ("000858", "五粮液"), ("000895", "双汇发展"),
    ("000938", "紫光股份"), ("000983", "山西焦煤"), ("001979", "招商蛇口"),
    ("002415", "海康威视"), ("002594", "比亚迪"), ("300750", "宁德时代"),
    ("600000", "浦发银行"), ("600036", "招商银行"), ("600519", "贵州茅台"),
    ("600887", "伊利股份"), ("601318", "中国平安"), ("601398", "工商银行"),
    ("601857", "中国石油"), ("601988", "中国银行"), ("603259", "药明康德"),
]


def _retry(func: Callable[[], T], retries: int = 3, delay: float = 0.8) -> T:
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc


SH_ETF_PREFIXES = ("510", "511", "512", "513", "515", "516", "518", "560", "561", "562", "563", "588")


def is_etf_code(code: str) -> bool:
    """判断是否为 A 股 ETF 代码。"""
    code = str(code).zfill(6)
    if not code.isdigit():
        return False
    prefix3 = code[:3]
    if prefix3 in SH_ETF_PREFIXES:
        return True
    if prefix3 == "159":
        return True
    return code[:2] in ("16", "18")


def price_step_for_code(code: str) -> float:
    """行情价格最小变动单位：ETF 0.001，股票 0.01。"""
    return 0.001 if is_etf_code(code) else 0.01


def code_to_symbol(code: str) -> str:
    """6 位代码转行情接口符号，如 600519 -> sh600519，563530 -> sh563530。"""
    code = str(code).zfill(6)
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    if code.startswith("6"):
        return f"sh{code}"
    if is_etf_code(code) and code[:3] in SH_ETF_PREFIXES:
        return f"sh{code}"
    return f"sz{code}"


def normalize_stock_list(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    result = df.copy()
    rename_map = {}
    for standard, aliases in COLUMN_ALIASES.items():
        if standard in result.columns:
            continue
        for alias in aliases:
            if alias in result.columns:
                rename_map[alias] = standard
                break

    if rename_map:
        result = result.rename(columns=rename_map)

    if "code" in result.columns:
        result["code"] = (
            result["code"].astype(str)
            .str.replace(r"\.(SH|SZ|BJ)$", "", regex=True)
            .str.replace(r"^(sh|sz|bj)\.", "", regex=True)
        )
        result["code"] = result["code"].str.zfill(6)

    if "market_cap" in result.columns:
        median = result["market_cap"].median()
        if pd.notna(median) and median > 1e6:
            result["market_cap"] = result["market_cap"] / 1e8

    return result.drop_duplicates(subset=["code"], keep="first")


def _save_stock_list_cache(df: pd.DataFrame, source: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(STOCK_LIST_CACHE, index=False, encoding="utf-8-sig")
    STOCK_LIST_META.write_text(
        json.dumps(
            {
                "source": source,
                "count": len(df),
                "updated_at": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_stock_list_cache(max_age_hours: Optional[int] = CACHE_MAX_AGE_HOURS) -> pd.DataFrame:
    if not STOCK_LIST_CACHE.exists():
        return pd.DataFrame()

    try:
        if STOCK_LIST_META.exists() and max_age_hours is not None:
            meta = json.loads(STOCK_LIST_META.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(meta["updated_at"])
            if datetime.now() - updated_at > timedelta(hours=max_age_hours):
                return pd.DataFrame()

        df = pd.read_csv(STOCK_LIST_CACHE, dtype={"code": str})
        return normalize_stock_list(df)
    except Exception:
        return pd.DataFrame()


def _load_stale_cache() -> pd.DataFrame:
    if not STOCK_LIST_CACHE.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(STOCK_LIST_CACHE, dtype={"code": str})
        return normalize_stock_list(df)
    except Exception:
        return pd.DataFrame()


def _fetch_offline_fallback() -> pd.DataFrame:
    return pd.DataFrame(OFFLINE_FALLBACK_STOCKS, columns=["code", "name"])


def _fetch_via_sina() -> pd.DataFrame:
    url = (
        "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData"
    )
    headers = {**DEFAULT_HEADERS, "Referer": "http://finance.sina.com.cn/"}
    rows: List[dict] = []
    page = 1

    while page <= 200:
        params = {
            "page": str(page),
            "num": "80",
            "sort": "symbol",
            "asc": "1",
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page",
        }

        def _request_page() -> list:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []

        batch = _retry(lambda: _request_page(), retries=3)
        if not batch:
            break

        rows.extend(batch)
        page += 1
        time.sleep(0.12)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return normalize_stock_list(df)


def _fetch_via_akshare() -> pd.DataFrame:
    import akshare as ak

    return normalize_stock_list(ak.stock_zh_a_spot_em())


def _fetch_via_qstock_get_code() -> pd.DataFrame:
    if _qs_get_code is None:
        return pd.DataFrame()
    return normalize_stock_list(_qs_get_code())


def _fetch_via_qstock_api() -> pd.DataFrame:
    import qstock as qs

    return normalize_stock_list(qs.get_data("stock_list"))


def _fetch_via_eastmoney_api() -> pd.DataFrame:
    hosts = [
        "https://push2.eastmoney.com",
        "http://82.push2.eastmoney.com",
        "http://80.push2.eastmoney.com",
        "http://22.push2.eastmoney.com",
    ]
    fields = "f12,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18,f20,f21,f23"
    fs = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"
    headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}

    for host in hosts:
        df_total = pd.DataFrame()
        page_number = 1
        page_size = 200
        url = f"{host}/api/qt/clist/get"

        try:
            while page_number <= 100:
                params = {
                    "pn": str(page_number),
                    "pz": str(page_size),
                    "po": "1",
                    "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": fs,
                    "fields": fields,
                }
                time.sleep(0.2)
                response = requests.get(url, headers=headers, params=params, timeout=20)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("data") or not payload["data"].get("diff"):
                    break

                batch = payload["data"]["diff"]
                df_total = pd.concat([df_total, pd.DataFrame(batch)], ignore_index=True)
                page_number += 1
                if not batch:
                    break

            if not df_total.empty:
                rename = {
                    "f12": "code",
                    "f14": "name",
                    "f2": "price",
                    "f3": "涨跌幅",
                    "f20": "market_cap",
                    "f23": "pb",
                    "f9": "pe",
                }
                existing = {k: v for k, v in rename.items() if k in df_total.columns}
                return normalize_stock_list(df_total.rename(columns=existing))
        except Exception:
            continue

    return pd.DataFrame()


def _fetch_via_baostock() -> pd.DataFrame:
    import baostock as bs

    lg = bs.login()
    if lg.error_code != "0":
        return pd.DataFrame()

    try:
        rs = bs.query_all_stock(day=datetime.now().strftime("%Y-%m-%d"))
        rows: List[List[str]] = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=rs.fields)
        df = df[df["code"].str.startswith(("sh.6", "sz.0", "sz.3", "bj."))]
        df["code"] = df["code"].str.replace(r"^(sh|sz|bj)\.", "", regex=True)
        df = df.rename(columns={"code_name": "name"})
        return normalize_stock_list(df[["code", "name"]])
    finally:
        bs.logout()


def _fetch_via_tencent_hist(
    code: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    symbol = code_to_symbol(code)
    start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    param = f"{symbol},day,{start_fmt},{end_fmt},640,qfq"
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def _request() -> pd.DataFrame:
        response = requests.get(
            url,
            params={"param": param},
            headers={**DEFAULT_HEADERS, "Referer": "https://gu.qq.com/"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        stock_data = payload.get("data", {}).get(symbol, {})
        rows = stock_data.get("qfqday") or stock_data.get("day") or []
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])
        for col in ("open", "close", "high", "low", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["pct_chg"] = df["close"].pct_change() * 100
        return df

    return _retry(_request, retries=3)


def _fetch_via_sina_hist(code: str, datalen: int = 120) -> pd.DataFrame:
    symbol = code_to_symbol(code)
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(datalen)}

    def _request() -> pd.DataFrame:
        response = requests.get(
            url,
            params=params,
            headers={**DEFAULT_HEADERS, "Referer": "https://finance.sina.com.cn/"},
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        rename = {"day": "date"}
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        for col in ("open", "close", "high", "low", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["pct_chg"] = df["close"].pct_change() * 100
        return df

    return _retry(_request, retries=3)


def _parse_tencent_qt_line(line: str) -> Optional[dict]:
    """解析腾讯 qt.gtimg.cn 行情串。"""
    line = line.strip()
    if not line or "~" not in line:
        return None
    if '="' in line:
        line = line.split('="', 1)[1].rstrip('";')
    parts = line.split("~")
    if len(parts) < 40:
        return None
    try:
        code = str(parts[2]).zfill(6)
        price = float(parts[3])
        pre_close = float(parts[4]) if parts[4] else 0.0
        open_price = float(parts[5]) if parts[5] else price
        pct_chg = float(parts[32]) if parts[32] else 0.0
        if pct_chg == 0 and pre_close > 0:
            pct_chg = (price - pre_close) / pre_close * 100
        quote_time = parts[30] if len(parts) > 30 else ""
        trade_date = datetime.now().strftime("%Y-%m-%d")
        if len(quote_time) >= 8:
            trade_date = f"{quote_time[:4]}-{quote_time[4:6]}-{quote_time[6:8]}"
        return {
            "code": code,
            "name": parts[1],
            "close": price,
            "price": price,
            "open": open_price,
            "pre_close": pre_close,
            "high": float(parts[33]) if parts[33] else price,
            "low": float(parts[34]) if parts[34] else price,
            "pct_chg": round(pct_chg, 2),
            "changepercent": round(pct_chg, 2),
            "volume": float(parts[36]) if parts[36] else 0.0,
            "amount": float(parts[37]) if parts[37] else 0.0,
            "turnover": float(parts[38]) if parts[38] else 0.0,
            "turnoverratio": float(parts[38]) if parts[38] else 0.0,
            "trade_date": trade_date,
            "quote_time": quote_time,
            "source": "tencent_qt",
        }
    except (ValueError, IndexError):
        return None


def get_realtime_quotes(codes: List[str], verbose: bool = False) -> pd.DataFrame:
    """批量获取腾讯实时/最新价（用于持仓估值与盘中扫描）。"""
    if not codes:
        return pd.DataFrame()

    unique_codes = sorted({str(c).zfill(6) for c in codes})
    symbols = [code_to_symbol(c) for c in unique_codes]
    url = "https://qt.gtimg.cn/q="
    headers = {**DEFAULT_HEADERS, "Referer": "https://gu.qq.com/"}
    rows: List[dict] = []

    for i in range(0, len(symbols), TENCENT_QT_BATCH):
        batch = symbols[i : i + TENCENT_QT_BATCH]
        try:
            response = requests.get(url + ",".join(batch), headers=headers, timeout=20)
            response.raise_for_status()
            for line in response.text.strip().split(";"):
                item = _parse_tencent_qt_line(line)
                if item:
                    rows.append(item)
        except Exception as exc:
            if verbose:
                print(f"腾讯行情批次失败: {exc}")
        time.sleep(0.12)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=["code"], keep="last")


def refresh_stock_list_quotes(
    df: pd.DataFrame,
    codes: Optional[List[str]] = None,
    verbose: bool = False,
) -> pd.DataFrame:
    """用腾讯最新价覆盖股票列表中的 price / pct_chg / turnover。"""
    if df.empty:
        return df

    result = df.copy()
    if "code" not in result.columns:
        return result

    result["code"] = result["code"].astype(str).str.zfill(6)
    target_codes = codes or result["code"].tolist()
    quotes = get_realtime_quotes(target_codes, verbose=verbose)
    if quotes.empty:
        return result

    quote_map = quotes.set_index("code")
    for col, qcol in (
        ("price", "close"),
        ("pct_chg", "pct_chg"),
        ("changepercent", "pct_chg"),
        ("turnover", "turnover"),
        ("turnoverratio", "turnover"),
    ):
        if qcol in quote_map.columns:
            mapped = result["code"].map(quote_map[qcol])
            if col not in result.columns:
                result[col] = mapped
            else:
                result[col] = mapped.combine_first(result[col])

    result["quote_time"] = result["code"].map(
        quote_map["quote_time"] if "quote_time" in quote_map.columns else pd.Series(dtype=str)
    )
    result["trade_date"] = result["code"].map(
        quote_map["trade_date"] if "trade_date" in quote_map.columns else pd.Series(dtype=str)
    )
    return result


def save_daily_close_snapshot(quotes: pd.DataFrame, trade_date: Optional[str] = None) -> Path:
    """保存每日收盘/最新价快照。"""
    DAILY_CLOSE_DIR.mkdir(parents=True, exist_ok=True)
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    if quotes.empty:
        raise ValueError("行情数据为空，无法保存")

    if "trade_date" in quotes.columns and quotes["trade_date"].notna().any():
        trade_date = str(quotes["trade_date"].dropna().iloc[0])[:10]

    path = DAILY_CLOSE_DIR / f"{trade_date}.csv"
    cols = [
        "code", "name", "close", "open", "high", "low", "pre_close",
        "pct_chg", "turnover", "volume", "amount", "trade_date", "quote_time", "source",
    ]
    out = quotes.copy()
    if "close" not in out.columns and "price" in out.columns:
        out["close"] = out["price"]
    for col in cols:
        if col not in out.columns:
            out[col] = ""
    out[cols].to_csv(path, index=False, encoding="utf-8-sig")

    meta = {
        "trade_date": trade_date,
        "count": len(out),
        "updated_at": datetime.now().isoformat(),
        "source": "tencent_qt",
    }
    path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_daily_close_snapshot(trade_date: Optional[str] = None) -> pd.DataFrame:
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    path = DAILY_CLOSE_DIR / f"{trade_date}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)
    return df


def collect_daily_market_close(
    stock_list: Optional[pd.DataFrame] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    采集全市场最新收盘/现价并落盘。
    超短扫描与仓位估值应优先使用此数据。
    """
    if stock_list is None:
        stock_list = get_all_stocks(verbose=verbose, use_cache=True, refresh_prices=False)

    if stock_list.empty:
        return pd.DataFrame()

    codes = stock_list["code"].astype(str).str.zfill(6).tolist()
    if verbose:
        print(f"正在采集 {len(codes)} 只股票最新行情（腾讯）...")
    quotes = get_realtime_quotes(codes, verbose=verbose)
    if quotes.empty:
        return pd.DataFrame()

    path = save_daily_close_snapshot(quotes)
    refreshed = refresh_stock_list_quotes(stock_list, codes=codes, verbose=False)
    _save_stock_list_cache(refreshed, "tencent_qt_daily")
    if verbose:
        print(f"已保存每日行情: {path} ({len(quotes)} 只)")
    return quotes


def get_market_spot(
    verbose: bool = False,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """
    获取带最新价的行情快照。
    优先今日 daily_close 文件，否则实时拉取腾讯行情。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    if not force_refresh:
        snap = load_daily_close_snapshot(today)
        if not snap.empty:
            if verbose:
                print(f"使用今日收盘快照: {today} ({len(snap)} 只)")
            base = get_all_stocks(verbose=False, use_cache=True, refresh_prices=False)
            if base.empty:
                return normalize_stock_list(snap)
            merged = base.copy()
            merged["code"] = merged["code"].astype(str).str.zfill(6)
            snap_idx = snap.set_index("code")
            for col in ("close", "pct_chg", "turnover", "trade_date", "quote_time"):
                src = "price" if col == "close" and "close" not in snap_idx.columns else col
                if src in snap_idx.columns or col in snap_idx.columns:
                    merged[col if col != "close" else "price"] = merged["code"].map(
                        snap_idx[col if col in snap_idx.columns else src]
                    )
            if "close" in snap_idx.columns:
                merged["price"] = merged["code"].map(snap_idx["close"])
            if "pct_chg" in snap_idx.columns:
                merged["pct_chg"] = merged["code"].map(snap_idx["pct_chg"])
                merged["changepercent"] = merged["pct_chg"]
            if "turnover" in snap_idx.columns:
                merged["turnover"] = merged["code"].map(snap_idx["turnover"])
            return merged

    quotes = collect_daily_market_close(verbose=verbose)
    if quotes.empty:
        base = get_all_stocks(verbose=verbose, use_cache=True, refresh_prices=False)
        return refresh_stock_list_quotes(base, verbose=verbose)
    base = get_all_stocks(verbose=False, use_cache=True, refresh_prices=False)
    return refresh_stock_list_quotes(base, codes=quotes["code"].tolist(), verbose=False)


def _patch_hist_with_quote(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """用最新行情修正 K 线最后一根（避免复权/缓存导致涨跌幅失真）。"""
    quotes = get_realtime_quotes([code])
    if quotes.empty or df.empty:
        return df

    q = quotes.iloc[0]
    close = float(q["close"])
    trade_date = pd.to_datetime(str(q["trade_date"])[:10])
    result = df.copy()
    result["date"] = pd.to_datetime(result["date"])

    if result.iloc[-1]["date"].date() == trade_date.date():
        idx = result.index[-1]
        result.loc[idx, "close"] = close
        if "open" in result.columns and q.get("open"):
            result.loc[idx, "open"] = float(q["open"])
        if "high" in result.columns and q.get("high"):
            result.loc[idx, "high"] = max(float(result.loc[idx, "high"]), close, float(q["high"]))
        if "low" in result.columns and q.get("low"):
            result.loc[idx, "low"] = min(float(result.loc[idx, "low"]), close, float(q["low"]))
    else:
        row = {
            "date": trade_date,
            "open": float(q.get("open", close)),
            "close": close,
            "high": float(q.get("high", close)),
            "low": float(q.get("low", close)),
            "volume": float(q.get("volume", 0)),
        }
        result = pd.concat([result, pd.DataFrame([row])], ignore_index=True)

    if "pct_chg" in result.columns or "close" in result.columns:
        result["pct_chg"] = result["close"].pct_change() * 100
        if q.get("pre_close") and float(q["pre_close"]) > 0:
            result.loc[result.index[-1], "pct_chg"] = (
                (close - float(q["pre_close"])) / float(q["pre_close"]) * 100
            )
    return result.sort_values("date").reset_index(drop=True)


def get_all_stocks(
    verbose: bool = True,
    use_cache: bool = True,
    allow_offline: bool = True,
    refresh_prices: bool = False,
) -> pd.DataFrame:
    """
    获取全部 A 股列表。

    优先级: 本地缓存 > 新浪 > AKShare > qstock > 东方财富 > baostock > 过期缓存 > 离线兜底
    """
    if use_cache:
        cached = _load_stock_list_cache()
        if not cached.empty:
            if verbose:
                print(f"使用本地缓存: {len(cached)} 只股票")
            if refresh_prices:
                if verbose:
                    print("刷新最新价（腾讯实时）...")
                cached = refresh_stock_list_quotes(cached, verbose=verbose)
                _save_stock_list_cache(cached, "tencent_qt")
            return cached

    providers = [
        ("新浪 API", _fetch_via_sina),
        ("AKShare", _fetch_via_akshare),
        ("qstock get_code", _fetch_via_qstock_get_code),
        ("qstock API", _fetch_via_qstock_api),
        ("东方财富 API", _fetch_via_eastmoney_api),
        ("baostock", _fetch_via_baostock),
    ]

    for name, fetcher in providers:
        try:
            if verbose:
                print(f"尝试数据源: {name}...")
            df = fetcher()
            if not df.empty:
                if verbose:
                    print(f"通过 {name} 获取 {len(df)} 只股票")
                if refresh_prices:
                    df = refresh_stock_list_quotes(df, verbose=verbose)
                    _save_stock_list_cache(df, "tencent_qt")
                else:
                    _save_stock_list_cache(df, name)
                return df
        except Exception as exc:
            if verbose:
                print(f"{name} 失败: {exc}")

    stale = _load_stale_cache()
    if not stale.empty:
        if verbose:
            print(f"在线源均失败，使用过期缓存: {len(stale)} 只股票")
        return stale

    if allow_offline:
        df = _fetch_offline_fallback()
        if verbose:
            print(f"在线源均失败，使用内置兜底列表: {len(df)} 只股票")
            print("提示: 网络恢复后重新运行可获取完整 A 股列表并更新 cache/stock_list.csv")
        return df

    if verbose:
        print("所有数据源均失败")
    return pd.DataFrame()


def get_stock_hist(
    code: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days: int = 180,
    freq: str = "d",
    patch_live: bool = True,
) -> pd.DataFrame:
    """获取单只股票历史 K 线。优先级: 腾讯 > qstock > AKShare > 新浪。"""
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    if start is None:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    code = str(code).zfill(6)
    hist_providers = [
        ("腾讯财经", lambda: _fetch_via_tencent_hist(code, start, end)),
    ]

    if _qs_available and _qs_web_data is not None:
        hist_providers.append(
            (
                "qstock",
                lambda: _qs_web_data(code, start=start, end=end, freq=freq, fqt=1),
            )
        )

    def _akshare_hist() -> pd.DataFrame:
        import akshare as ak

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="qfq",
        )
        if df.empty:
            return df
        rename = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_chg",
        }
        return df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    hist_providers.extend(
        [
            ("AKShare", _akshare_hist),
            ("新浪 K 线", lambda: _fetch_via_sina_hist(code, datalen=max(days, 30))),
        ]
    )

    for _, fetcher in hist_providers:
        try:
            df = fetcher()
            if isinstance(df, pd.DataFrame) and not df.empty:
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                if patch_live:
                    df = _patch_hist_with_quote(df, code)
                if "pct_chg" not in df.columns and "close" in df.columns:
                    df["pct_chg"] = df["close"].pct_change() * 100
                return df
        except Exception:
            continue

    return pd.DataFrame()


def get_stock_recent_bars(code: str, days: int = 10) -> list[dict]:
    """最近 N 个交易日 K 线（用于个股下钻）。"""
    days = max(1, min(int(days), 60))
    df = get_stock_hist(code, days=max(days * 4, 30))
    if df.empty:
        return []

    tail = df.tail(days)
    bars: list[dict] = []
    for _, row in tail.iterrows():
        d = row.get("date")
        if hasattr(d, "strftime"):
            date_str = d.strftime("%Y-%m-%d")
        else:
            date_str = str(d)[:10]

        close = float(row.get("close") or 0)
        open_ = float(row.get("open") or close)
        high = float(row.get("high") or close)
        low = float(row.get("low") or close)
        pct = row.get("pct_chg")
        if pct is None or (isinstance(pct, float) and pd.isna(pct)):
            pct_val = None
        else:
            pct_val = round(float(pct), 2)

        vol = row.get("volume")
        bars.append(
            {
                "date": date_str,
                "open": round(open_, 2),
                "close": round(close, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "pct_chg": pct_val,
                "volume": int(vol) if vol is not None and not pd.isna(vol) else None,
            }
        )
    return bars


def get_latest_price(code: str) -> float:
    """获取单只股票最新价（腾讯实时）。"""
    quotes = get_realtime_quotes([code])
    if quotes.empty:
        return 0.0
    return float(quotes.iloc[0]["close"])


def get_stock_code_column(df: pd.DataFrame) -> str:
    for col in ("code", "代码"):
        if col in df.columns:
            return col
    raise ValueError("股票列表缺少代码列")


def get_stock_name_column(df: pd.DataFrame) -> Optional[str]:
    for col in ("name", "名称"):
        if col in df.columns:
            return col
    return None


def _clean_display_name(name: str) -> str:
    import re

    return re.sub(r"^\d+[~～]?", "", str(name or "").strip()).strip()


def _instrument_base_df() -> pd.DataFrame:
    """代码+名称索引（优先本地缓存，不拉实时价）。"""
    df = _load_stale_cache()
    if df.empty or "name" not in df.columns:
        df = get_all_stocks(
            verbose=False,
            use_cache=True,
            allow_offline=True,
            refresh_prices=False,
        )
    if df.empty or "code" not in df.columns or "name" not in df.columns:
        return pd.DataFrame()
    out = df[["code", "name"]].copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["name"] = out["name"].astype(str).map(_clean_display_name)
    return out.drop_duplicates(subset=["code"], keep="first")


def lookup_instrument_by_code(code: str) -> Optional[dict]:
    code = str(code or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    df = _instrument_base_df()
    if df.empty:
        return None
    row = df[df["code"] == code]
    if row.empty:
        return None
    name = str(row.iloc[0]["name"]).strip()
    return {"code": code, "name": name, "is_etf": is_etf_code(code)}


def lookup_instrument_by_name(name: str, limit: int = 8) -> List[dict]:
    """按名称查代码：精确 > 前缀 > 包含，仅返回唯一或少量候选。"""
    import re

    query = _clean_display_name(str(name or "").strip())
    if not query:
        return []

    m = re.match(r"^(.+?)\s*\((\d{6})\)\s*$", query)
    if m:
        code = m.group(2)
        hit = lookup_instrument_by_code(code)
        return [hit] if hit else []

    df = _instrument_base_df()
    if df.empty:
        return []

    names = df["name"].astype(str)
    exact = df[names == query]
    if len(exact) == 1:
        return [_row_to_instrument(exact.iloc[0])]

    prefix = df[names.str.startswith(query, na=False)]
    if len(prefix) == 1:
        return [_row_to_instrument(prefix.iloc[0])]

    contains = df[names.str.contains(re.escape(query), regex=True, na=False)]
    if len(contains) == 1:
        return [_row_to_instrument(contains.iloc[0])]

    if len(contains) > 1:
        return [_row_to_instrument(row) for _, row in contains.head(limit).iterrows()]

    if len(prefix) > 1:
        return [_row_to_instrument(row) for _, row in prefix.head(limit).iterrows()]

    return []


def _row_to_instrument(row: pd.Series) -> dict:
    code = str(row["code"]).zfill(6)
    return {
        "code": code,
        "name": str(row["name"]).strip(),
        "is_etf": is_etf_code(code),
    }


def code_to_secid(code: str) -> str:
    """6 位代码转东财 secid。"""
    code = str(code).zfill(6)
    if code.startswith(("4", "8", "92")):
        return f"0.{code}"
    if code.startswith("6"):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_fundamentals_via_eastmoney() -> pd.DataFrame:
    """东财 A 股列表：PE / PB / 净利润同比。"""
    hosts = [
        "https://push2.eastmoney.com",
        "http://82.push2.eastmoney.com",
        "http://80.push2.eastmoney.com",
    ]
    fields = "f12,f14,f9,f23,f184"
    fs = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"
    headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}

    for host in hosts:
        df_total = pd.DataFrame()
        page_number = 1
        url = f"{host}/api/qt/clist/get"
        try:
            while page_number <= 100:
                params = {
                    "pn": str(page_number),
                    "pz": "200",
                    "po": "1",
                    "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": fs,
                    "fields": fields,
                }
                time.sleep(0.15)
                response = requests.get(url, headers=headers, params=params, timeout=20)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("data") or not payload["data"].get("diff"):
                    break
                batch = payload["data"]["diff"]
                df_total = pd.concat([df_total, pd.DataFrame(batch)], ignore_index=True)
                page_number += 1
                if not batch:
                    break

            if df_total.empty:
                continue

            rename = {
                "f12": "code",
                "f14": "name",
                "f9": "pe",
                "f23": "pb",
                "f184": "profit_yoy",
            }
            existing = {k: v for k, v in rename.items() if k in df_total.columns}
            out = df_total.rename(columns=existing)
            out["code"] = out["code"].astype(str).str.zfill(6)
            for col in ("pe", "pb", "profit_yoy"):
                if col in out.columns:
                    out[col] = pd.to_numeric(out[col], errors="coerce")
            keep = [c for c in ("code", "name", "pe", "pb", "profit_yoy") if c in out.columns]
            return out[keep].drop_duplicates(subset=["code"], keep="first")
        except Exception:
            continue
    return pd.DataFrame()


def _save_fundamentals_cache(df: pd.DataFrame, source: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(STOCK_FUNDAMENTALS_CACHE, index=False, encoding="utf-8-sig")
    STOCK_FUNDAMENTALS_META.write_text(
        json.dumps(
            {
                "source": source,
                "count": len(df),
                "updated_at": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_fundamentals_cache(max_age_hours: Optional[int] = CACHE_MAX_AGE_HOURS) -> pd.DataFrame:
    if not STOCK_FUNDAMENTALS_CACHE.exists():
        return pd.DataFrame()
    try:
        if STOCK_FUNDAMENTALS_META.exists() and max_age_hours is not None:
            meta = json.loads(STOCK_FUNDAMENTALS_META.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(meta["updated_at"])
            if datetime.now() - updated_at > timedelta(hours=max_age_hours):
                return pd.DataFrame()
        df = pd.read_csv(STOCK_FUNDAMENTALS_CACHE, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df
    except Exception:
        return pd.DataFrame()


def get_stock_fundamentals(verbose: bool = False, use_cache: bool = True) -> pd.DataFrame:
    """全市场 PE / PB / 净利润同比（本地缓存 24h）。"""
    if use_cache:
        cached = _load_fundamentals_cache()
        if not cached.empty:
            if verbose:
                print(f"使用基本面缓存: {len(cached)} 只")
            return cached

    for name, fetcher in (
        ("东财 API", _fetch_fundamentals_via_eastmoney),
        ("AKShare", lambda: _merge_fundamentals_from_spot(_fetch_via_akshare())),
    ):
        try:
            if verbose:
                print(f"拉取基本面: {name}...")
            df = fetcher()
            if not df.empty:
                if verbose:
                    print(f"基本面 {len(df)} 只 ({name})")
                _save_fundamentals_cache(df, name)
                return df
        except Exception as exc:
            if verbose:
                print(f"{name} 基本面失败: {exc}")

    stale = _load_fundamentals_cache(max_age_hours=None)
    if not stale.empty:
        return stale
    return pd.DataFrame()


def _merge_fundamentals_from_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "code" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    cols = {"code": "code"}
    for std in ("pe", "pb", "profit_yoy"):
        for alias in COLUMN_ALIASES.get(std, [std]):
            if alias in out.columns:
                cols[alias] = std
                break
    out = out.rename(columns=cols)
    keep = [c for c in ("code", "pe", "pb", "profit_yoy") if c in out.columns]
    return out[keep].drop_duplicates(subset=["code"], keep="first")


def get_fundamental_map() -> dict[str, dict]:
    df = get_stock_fundamentals(verbose=False, use_cache=True)
    if df.empty:
        return {}
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        code = str(row["code"]).zfill(6)
        item: dict = {}
        for col in ("pe", "pb", "profit_yoy"):
            if col in row and pd.notna(row[col]):
                try:
                    val = float(row[col])
                    if col == "profit_yoy" and val in (-9999, 9999):
                        continue
                    item[col] = round(val, 2)
                except (TypeError, ValueError):
                    pass
        out[code] = item
    return out


def _load_industry_cache() -> dict[str, str]:
    if not STOCK_INDUSTRY_CACHE.exists():
        return {}
    try:
        raw = json.loads(STOCK_INDUSTRY_CACHE.read_text(encoding="utf-8"))
        return {str(k).zfill(6): str(v) for k, v in raw.items() if v}
    except Exception:
        return {}


def _save_industry_cache(mapping: dict[str, str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STOCK_INDUSTRY_CACHE.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_stock_industry(code: str) -> str:
    """单只股票所属行业（东财）。"""
    code = str(code).zfill(6)
    try:
        response = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": code_to_secid(code), "fields": "f127"},
            headers={**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"},
            timeout=12,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        industry = str(data.get("f127") or "").strip()
        if industry and industry != "-":
            return industry
    except Exception:
        pass
    return ""


def ensure_industry_map(codes: List[str], verbose: bool = False) -> dict[str, str]:
    """补齐股票行业映射（增量写入缓存）。"""
    mapping = _load_industry_cache()
    missing = [str(c).zfill(6) for c in codes if str(c).zfill(6) not in mapping]
    if not missing:
        return mapping

    if verbose:
        print(f"  拉取行业数据 {len(missing)} 只…")
    for i, code in enumerate(missing, 1):
        industry = fetch_stock_industry(code)
        if industry:
            mapping[code] = industry
        if verbose and (i == 1 or i % 20 == 0 or i == len(missing)):
            print(f"  行业进度 {i}/{len(missing)}", flush=True)
        time.sleep(0.08)

    if missing:
        _save_industry_cache(mapping)
    return mapping


def list_industries_from_map(mapping: Optional[dict[str, str]] = None) -> List[str]:
    src = mapping if mapping is not None else _load_industry_cache()
    items = sorted({v.strip() for v in src.values() if v and v.strip()})
    return items


def get_instrument_index() -> tuple[List[dict], str]:
    df = _instrument_base_df()
    updated_at = ""
    if STOCK_LIST_META.exists():
        try:
            meta = json.loads(STOCK_LIST_META.read_text(encoding="utf-8"))
            updated_at = str(meta.get("updated_at") or "")
        except Exception:
            pass
    items = [_row_to_instrument(row) for _, row in df.iterrows()]
    return items, updated_at


EM_BOARD_HOSTS = [
    "https://17.push2.eastmoney.com",
    "https://push2.eastmoney.com",
    "https://79.push2.eastmoney.com",
    "https://29.push2.eastmoney.com",
    "http://82.push2.eastmoney.com",
    "http://80.push2.eastmoney.com",
]

BOARD_LIST_FS = {
    "concept": "m:90 t:3 f:!50",
    "industry": "m:90 t:2 f:!50",
}

BOARD_LIST_FIELDS = "f3,f8,f12,f14,f104,f105,f128,f136,f140"


def _board_cache_fresh(path: Path, max_age_minutes: int = BOARD_CACHE_MAX_AGE_MINUTES) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = payload.get("updated_at")
        if not updated_at:
            return False
        ts = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
        return datetime.now() - ts < timedelta(minutes=max_age_minutes)
    except Exception:
        return False


def _fetch_em_clist(
    fs: str,
    fields: str,
    fid: str = "f3",
    page_size: int = 100,
    max_pages: int = 20,
) -> List[dict]:
    headers = {**DEFAULT_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    for host in EM_BOARD_HOSTS:
        rows: List[dict] = []
        url = f"{host}/api/qt/clist/get"
        try:
            for page in range(1, max_pages + 1):
                params = {
                    "pn": str(page),
                    "pz": str(page_size),
                    "po": "1",
                    "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2",
                    "invt": "2",
                    "fid": fid,
                    "fs": fs,
                    "fields": fields,
                }
                time.sleep(0.12)
                response = requests.get(url, headers=headers, params=params, timeout=20)
                response.raise_for_status()
                data = response.json().get("data") or {}
                batch = data.get("diff") or []
                if not batch:
                    break
                rows.extend(batch)
                total = int(data.get("total") or 0)
                if total and len(rows) >= total:
                    break
            if rows:
                return rows
        except Exception:
            continue
    return []


def _fetch_board_list_akshare(board_type: str) -> List[dict]:
    """akshare 备用：板块列表。"""
    try:
        import akshare as ak
    except ImportError:
        return []
    try:
        if board_type == "industry":
            df = ak.stock_board_industry_name_em()
        else:
            df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return []
        items: List[dict] = []
        for _, row in df.iterrows():
            try:
                pct_chg = float(row.get("涨跌幅") or 0)
            except (TypeError, ValueError):
                pct_chg = 0.0
            try:
                turnover = float(row.get("换手率") or 0)
            except (TypeError, ValueError):
                turnover = 0.0
            try:
                up_count = int(float(row.get("上涨家数") or 0))
            except (TypeError, ValueError):
                up_count = 0
            try:
                down_count = int(float(row.get("下跌家数") or 0))
            except (TypeError, ValueError):
                down_count = 0
            try:
                leader_pct = float(row.get("领涨股票-涨跌幅") or 0)
            except (TypeError, ValueError):
                leader_pct = 0.0
            code = str(row.get("板块代码") or "").strip()
            name = str(row.get("板块名称") or "").strip()
            if not code:
                continue
            board_score = round(
                pct_chg * 2 + up_count * 0.35 - down_count * 0.15 + min(turnover, 15) * 0.2,
                2,
            )
            items.append({
                "code": code,
                "name": name,
                "pct_chg": round(pct_chg, 2),
                "turnover": round(turnover, 2),
                "up_count": up_count,
                "down_count": down_count,
                "leader_name": str(row.get("领涨股票") or "").strip(),
                "leader_code": "",
                "leader_pct": round(leader_pct, 2),
                "board_score": board_score,
            })
        items.sort(key=lambda x: x.get("board_score", 0), reverse=True)
        return items
    except Exception:
        return []


def _fetch_board_constituents_akshare(board_code: str) -> List[dict]:
    """akshare 备用：板块成份。"""
    try:
        import akshare as ak
    except ImportError:
        return []
    board_code = str(board_code or "").strip().upper()
    if not board_code:
        return []
    try:
        if board_code.startswith("BK"):
            fetcher = ak.stock_board_concept_cons_em
            try:
                df = fetcher(symbol=board_code)
            except Exception:
                df = ak.stock_board_industry_cons_em(symbol=board_code)
        else:
            return []
        if df is None or df.empty:
            return []
        items: List[dict] = []
        for _, row in df.iterrows():
            code = str(row.get("代码") or "").strip().zfill(6)
            name = str(row.get("名称") or "").strip()
            if not code or not name:
                continue
            try:
                price = float(row.get("最新价") or 0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                pct_chg = float(row.get("涨跌幅") or 0)
            except (TypeError, ValueError):
                pct_chg = 0.0
            try:
                turnover = float(row.get("换手率") or 0)
            except (TypeError, ValueError):
                turnover = 0.0
            items.append({
                "code": code,
                "name": name,
                "price": round(price, 2),
                "pct_chg": round(pct_chg, 2),
                "turnover": round(turnover, 2),
            })
        items.sort(key=lambda x: x.get("pct_chg", 0), reverse=True)
        return items
    except Exception:
        return []


def _load_stale_board_cache(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("items") or []
    except Exception:
        return []


def _parse_board_row(row: dict) -> dict:
    code = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip()
    leader_name = str(row.get("f128") or "").strip()
    leader_code = str(row.get("f140") or "").strip().zfill(6) if row.get("f140") else ""
    try:
        leader_pct = float(row.get("f136") or 0)
    except (TypeError, ValueError):
        leader_pct = 0.0
    try:
        pct_chg = float(row.get("f3") or 0)
    except (TypeError, ValueError):
        pct_chg = 0.0
    try:
        turnover = float(row.get("f8") or 0)
    except (TypeError, ValueError):
        turnover = 0.0
    try:
        up_count = int(float(row.get("f104") or 0))
    except (TypeError, ValueError):
        up_count = 0
    try:
        down_count = int(float(row.get("f105") or 0))
    except (TypeError, ValueError):
        down_count = 0
    board_score = round(pct_chg * 2 + up_count * 0.35 - down_count * 0.15 + min(turnover, 15) * 0.2, 2)
    return {
        "code": code,
        "name": name,
        "pct_chg": round(pct_chg, 2),
        "turnover": round(turnover, 2),
        "up_count": up_count,
        "down_count": down_count,
        "leader_name": leader_name,
        "leader_code": leader_code,
        "leader_pct": round(leader_pct, 2),
        "board_score": board_score,
    }


def fetch_board_list(
    board_type: str = "concept",
    force_refresh: bool = False,
) -> List[dict]:
    """拉取概念/行业板块列表（东财，带缓存）。"""
    board_type = board_type if board_type in BOARD_LIST_FS else "concept"
    cache_path = BOARD_LIST_CACHE[board_type]
    if not force_refresh and _board_cache_fresh(cache_path):
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return payload.get("items") or []
        except Exception:
            pass

    rows = _fetch_em_clist(
        BOARD_LIST_FS[board_type],
        BOARD_LIST_FIELDS,
        fid="f3",
        page_size=100,
        max_pages=10,
    )
    items = [_parse_board_row(r) for r in rows if r.get("f12")]
    if not items:
        items = _fetch_board_list_akshare(board_type)
    if not items and cache_path.exists():
        items = _load_stale_board_cache(cache_path)
    items.sort(key=lambda x: x.get("board_score", 0), reverse=True)

    if items:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "board_type": board_type,
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return items


def fetch_board_constituents(
    board_code: str,
    force_refresh: bool = False,
) -> List[dict]:
    """拉取板块成份股（东财，带缓存）。"""
    board_code = str(board_code or "").strip().upper()
    if not board_code:
        return []

    BOARD_CONS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = BOARD_CONS_CACHE_DIR / f"{board_code}.json"
    if not force_refresh and _board_cache_fresh(cache_path, max_age_minutes=20):
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            return payload.get("items") or []
        except Exception:
            pass

    fields = "f2,f3,f8,f12,f14"
    rows = _fetch_em_clist(
        f"b:{board_code} f:!50",
        fields,
        fid="f3",
        page_size=100,
        max_pages=15,
    )
    items: List[dict] = []
    for row in rows:
        code = str(row.get("f12") or "").strip().zfill(6)
        name = str(row.get("f14") or "").strip()
        if not code or not name:
            continue
        try:
            price = float(row.get("f2") or 0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            pct_chg = float(row.get("f3") or 0)
        except (TypeError, ValueError):
            pct_chg = 0.0
        try:
            turnover = float(row.get("f8") or 0)
        except (TypeError, ValueError):
            turnover = 0.0
        items.append({
            "code": code,
            "name": name,
            "price": round(price, 2),
            "pct_chg": round(pct_chg, 2),
            "turnover": round(turnover, 2),
        })
    items.sort(key=lambda x: x.get("pct_chg", 0), reverse=True)
    if not items:
        items = _fetch_board_constituents_akshare(board_code)
    if not items and cache_path.exists():
        items = _load_stale_board_cache(cache_path)

    if items:
        cache_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "board_code": board_code,
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return items
