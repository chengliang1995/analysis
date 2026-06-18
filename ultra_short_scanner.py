"""
超短个股捕捉
基于行情快照 + K 线深度分析，筛选短线强势标的。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from qstock_strategy_optimizer import StrategyOptimizer
from stock_data import (
    get_market_spot,
    get_stock_code_column,
    get_stock_hist,
    get_stock_name_column,
)


class UltraShortScanner:
  """超短选股：涨停、连板、高换手、量价异动、涨停不破开。"""

  def __init__(self):
    self.optimizer = StrategyOptimizer()

  def _limit_threshold(self, code: str) -> float:
    """涨停阈值：主板 9.8%，创业板/科创板 19.5%。"""
    code = str(code).zfill(6)
    if code.startswith(("300", "301", "688", "689")):
      return 19.5
    return 9.8

  def _count_consecutive_limit_ups(self, df: pd.DataFrame, code: str) -> int:
    if df.empty or "close" not in df.columns:
      return 0
    pct = df["pct_chg"] if "pct_chg" in df.columns else df["close"].pct_change() * 100
    threshold = self._limit_threshold(code)
    count = 0
    for i in range(len(pct) - 1, -1, -1):
      if pd.notna(pct.iloc[i]) and pct.iloc[i] >= threshold - 0.2:
        count += 1
      else:
        break
    return count

  def _volume_ratio(self, df: pd.DataFrame, window: int = 5) -> float:
    if df.empty or "volume" not in df.columns or len(df) < window + 1:
      return 1.0
    vol = pd.to_numeric(df["volume"], errors="coerce")
    avg = vol.iloc[-window - 1 : -1].mean()
    if pd.isna(avg) or avg <= 0:
      return 1.0
    return float(vol.iloc[-1] / avg)

  def _analyze_single(
    self,
    code: str,
    name: str,
    spot: Optional[dict] = None,
    lookback_days: int = 10,
  ) -> Optional[Dict]:
    hist = get_stock_hist(code, days=lookback_days + 25)
    if hist.empty or len(hist) < 5:
      return None

    latest = hist.iloc[-1]
    pct_chg = float(latest.get("pct_chg", 0) or 0)
    threshold = self._limit_threshold(code)

    spot_pct = float(spot.get("changepercent", pct_chg)) if spot else pct_chg
    turnover = float(spot.get("turnover", spot.get("turnoverratio", 0)) or 0) if spot else 0
    price = float(spot.get("price", latest["close"]) or latest["close"]) if spot else float(latest["close"])

    consecutive = self._count_consecutive_limit_ups(hist, code)
    vol_ratio = self._volume_ratio(hist)
    limit_signal = self.optimizer.check_limit_up_signal(hist, lookback_days)

    ma5 = hist["close"].rolling(5).mean().iloc[-1]
    above_ma5 = price >= ma5 * 0.99 if pd.notna(ma5) else True

    # 近 3 日累计涨幅
    if len(hist) >= 3:
      gain_3d = (hist["close"].iloc[-1] / hist["close"].iloc[-3] - 1) * 100
    else:
      gain_3d = pct_chg

    score = 0
    tags: List[str] = []

    if spot_pct >= threshold - 0.3:
      score += 30
      tags.append("涨停")
    elif spot_pct >= 7:
      score += 18
      tags.append("强势")
    elif spot_pct >= 5:
      score += 10
      tags.append("异动")

    if consecutive >= 3:
      score += 25
      tags.append(f"{consecutive}连板")
    elif consecutive == 2:
      score += 18
      tags.append("2连板")
    elif consecutive == 1 and spot_pct >= threshold - 0.3:
      score += 12
      tags.append("首板")

    if turnover >= 15:
      score += 15
      tags.append("超高换手")
    elif turnover >= 8:
      score += 10
      tags.append("高换手")
    elif turnover >= 5:
      score += 5

    if vol_ratio >= 2.5:
      score += 12
      tags.append("放量")
    elif vol_ratio >= 1.8:
      score += 6

    if limit_signal:
      score += 15
      tags.append("涨停不破开")

    if above_ma5:
      score += 5

    if gain_3d >= 20:
      score += 8
      tags.append("3日强势")

    # 超短入选门槛
    if score < 25:
      return None

    return {
      "code": str(code).zfill(6),
      "name": name,
      "price": round(price, 2),
      "pct_chg": round(spot_pct, 2),
      "turnover": round(turnover, 2),
      "consecutive_boards": consecutive,
      "volume_ratio": round(vol_ratio, 2),
      "gain_3d": round(gain_3d, 2),
      "ultra_short_score": score,
      "tags": ",".join(tags),
      "has_limit_hold": bool(limit_signal),
      "limit_date": str(limit_signal["limit_date"]) if limit_signal else "",
      "days_after_limit": limit_signal.get("days_after_limit", "") if limit_signal else "",
    }

  def prefilter_spot(self, stock_list: pd.DataFrame, min_pct: float = 3.0, min_turnover: float = 2.0) -> pd.DataFrame:
    """从行情快照初筛，减少深度分析数量。"""
    df = stock_list.copy()
    pct_col = next((c for c in ("涨跌幅", "changepercent", "pct_chg") if c in df.columns), None)
    turnover_col = next((c for c in ("turnover", "turnoverratio", "换手率") if c in df.columns), None)

    if pct_col:
      df["_pct"] = pd.to_numeric(df[pct_col], errors="coerce").fillna(0)
    else:
      df["_pct"] = 0

    if turnover_col:
      df["_turnover"] = pd.to_numeric(df[turnover_col], errors="coerce").fillna(0)
    else:
      df["_turnover"] = 0

    # 强势 OR 高换手
    mask = (df["_pct"] >= min_pct) | (df["_turnover"] >= min_turnover)
    filtered = df[mask].copy()
    filtered["_sort"] = filtered["_pct"] * 0.6 + filtered["_turnover"] * 0.4
    return filtered.sort_values("_sort", ascending=False)

  def scan_market(
    self,
    stock_list: Optional[pd.DataFrame] = None,
    top_prefilter: int = 300,
    max_workers: int = 8,
    lookback_days: int = 10,
    min_score: int = 35,
    show_progress: bool = True,
  ) -> pd.DataFrame:
    """
    全市场超短扫描。

    1. 行情快照初筛（涨幅/换手）
    2. 对候选股做 K 线深度评分
    """
    if stock_list is None:
      if show_progress:
        print("加载股票列表并刷新最新价...")
      stock_list = get_market_spot(verbose=show_progress, force_refresh=False)

    if stock_list.empty:
      return pd.DataFrame()

    candidates = self.prefilter_spot(stock_list).head(top_prefilter)
    code_col = get_stock_code_column(candidates)
    name_col = get_stock_name_column(candidates)

    if show_progress:
      print(f"初筛 {len(candidates)} 只，开始深度分析...")

    tasks = []
    for _, row in candidates.iterrows():
      code = str(row[code_col]).zfill(6)
      name = str(row[name_col]) if name_col else code
      spot = {
        "changepercent": row.get("_pct", 0),
        "turnover": row.get("_turnover", 0),
        "price": row.get("price", row.get("trade", 0)),
      }
      tasks.append((code, name, spot))

    results: List[Dict] = []
    done = 0
    total = len(tasks)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
      futures = {
        executor.submit(self._analyze_single, code, name, spot, lookback_days): code
        for code, name, spot in tasks
      }
      for future in as_completed(futures):
        done += 1
        if show_progress and done % 50 == 0:
          print(f"  进度 {done}/{total}")
        try:
          item = future.result()
          if item and item["ultra_short_score"] >= min_score:
            results.append(item)
        except Exception:
          pass

    if not results:
      return pd.DataFrame()

    df = pd.DataFrame(results)
    return df.sort_values("ultra_short_score", ascending=False).reset_index(drop=True)

  def scan_codes(self, codes: List[str], lookback_days: int = 10) -> pd.DataFrame:
    """扫描指定代码列表。"""
    results = []
    for code in codes:
      item = self._analyze_single(str(code).zfill(6), code, None, lookback_days)
      if item:
        results.append(item)
    if not results:
      return pd.DataFrame()
    return pd.DataFrame(results).sort_values("ultra_short_score", ascending=False)
