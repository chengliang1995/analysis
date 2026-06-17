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

# 路径与缓存
BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
STOCK_LIST_CACHE = CACHE_DIR / "stock_list.csv"
STOCK_LIST_META = CACHE_DIR / "stock_list_meta.json"
CACHE_MAX_AGE_HOURS = 24

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


def code_to_symbol(code: str) -> str:
    """6 位代码转行情接口符号，如 600519 -> sh600519。"""
    code = str(code).zfill(6)
    if code.startswith(("4", "8", "92")):
        return f"bj{code}"
    if code.startswith("6"):
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


def get_all_stocks(
    verbose: bool = True,
    use_cache: bool = True,
    allow_offline: bool = True,
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
                if "pct_chg" not in df.columns and "close" in df.columns:
                    df["pct_chg"] = df["close"].pct_change() * 100
                return df
        except Exception:
            continue

    return pd.DataFrame()


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
