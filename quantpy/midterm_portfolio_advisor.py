"""
实盘中线顾问
- 个股复盘：趋势、均线、RSI、支撑阻力
- 持仓优化：仓位配比、加减仓建议
- 个股推荐：中线趋势 + 适度动量筛选
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from quantpy.paths import MIDTERM_OUTPUT_DIR
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import (
    ensure_industry_map,
    get_fundamental_map,
    get_market_spot,
    get_stock_hist,
    get_stock_code_column,
    get_stock_name_column,
)

ULTRA_SHORT_STRATEGIES = frozenset({"超短", "涨停"})


def _classify_bucket(strategy: str) -> str:
    return "ultra_short" if str(strategy) in ULTRA_SHORT_STRATEGIES else "midterm"

OUTPUT_DIR = MIDTERM_OUTPUT_DIR
MIDTERM_STRATEGIES = {"中线", "趋势", "手动", "价值", "波段", "ETF"}

PERFORMANCE_FILTER_OPTIONS = {
    "profit_growth": "净利正增长",
    "high_growth": "净利增≥30%",
    "low_pe": "低市盈率(0-30)",
    "value_growth": "低PE+正增长",
}

# 中线选股条件（技术面多为加分项，非全部硬性）
MIDTERM_SELECT_CONDITIONS = [
    {"id": "cap_range", "label": "市值150-1000亿", "category": "基本面"},
    {"id": "price_cap", "label": "股价<100元", "category": "基本面"},
    {"id": "ma_bull", "label": "站上MA20/均线多头", "category": "技术面"},
    {"id": "ma_rise", "label": "均线上移", "category": "技术面"},
    {"id": "volume_surge", "label": "放量上涨", "category": "技术面"},
    {"id": "daily_gain", "label": "近3日1日涨幅>2%", "category": "技术面"},
    {"id": "rsi_band", "label": "RSI 40-70", "category": "技术面"},
    {"id": "trend_20d", "label": "20日涨幅≥0", "category": "技术面"},
]

_CONDITION_LABELS = {c["id"]: c["label"] for c in MIDTERM_SELECT_CONDITIONS}
MIDTERM_MIN_SCORE = 55


def get_midterm_select_conditions() -> List[dict]:
    return list(MIDTERM_SELECT_CONDITIONS)


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
) -> Optional[dict]:
    """
    中线技术面评估（放宽版）：保留趋势主线，其余为加分项。
    硬筛：非极端涨跌、非深度跌势、具备基本多头结构。
    """
    close = pd.to_numeric(hist["close"], errors="coerce")
    if len(close) < 20:
        return None

    price = float(close.iloc[-1])
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma10 = float(close.rolling(10).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
    rsi = _rsi(close)
    ret_20d = _safe_pct(price, float(close.iloc[-21])) if len(close) >= 21 else 0
    pct = _hist_pct_series(hist)

    if spot_pct >= 9.5 or spot_pct <= -9.5:
        return None
    if turnover > 20:
        return None
    if ret_20d < -12:
        return None
    if rsi > 82:
        return None

    tags: List[str] = []
    conditions: List[str] = ["cap_range", "price_cap"]
    score = 20

    trend_ok = False
    if ma5 > ma10 > ma20:
        score += 22
        tags.append("均线多头")
        conditions.append("ma_bull")
        trend_ok = True
    elif price >= ma20 and ma5 >= ma10:
        score += 14
        tags.append("趋势向上")
        conditions.append("ma_bull")
        trend_ok = True
    elif price >= ma20 * 0.97:
        score += 10
        tags.append("站上MA20")
        conditions.append("ma_bull")
        trend_ok = True
    elif price >= ma10 and ma5 >= float(close.rolling(5).mean().iloc[-2]):
        score += 8
        tags.append("短期转强")
        trend_ok = True

    if not trend_ok:
        return None

    if len(close) >= 25:
        ma10_prev = float(close.rolling(10).mean().iloc[-6])
        ma20_prev = float(close.rolling(20).mean().iloc[-6])
        if ma10 > ma10_prev and ma20 > ma20_prev:
            score += 10
            tags.append("均线上移")
            conditions.append("ma_rise")

    if "volume" in hist.columns:
        vol = pd.to_numeric(hist["volume"], errors="coerce").fillna(0)
        if len(vol) >= 13 and vol.iloc[-10:].sum() > 0:
            vol_3 = float(vol.iloc[-3:].mean())
            vol_prev = float(vol.iloc[-10:-3].mean())
            rising_price = price > float(close.iloc[-4])
            if vol_prev > 0 and vol_3 >= vol_prev * 1.05 and rising_price:
                score += 12
                tags.append("放量上涨")
                conditions.append("volume_surge")

    if len(pct) >= 3:
        recent = pct.iloc[-3:]
        if int((recent > min_daily_gain_pct).sum()) >= 1:
            score += 10
            tags.append("近期强势")
            conditions.append("daily_gain")

    if 40 <= rsi <= 70:
        score += 8
        tags.append("RSI适中")
        conditions.append("rsi_band")

    if ret_20d >= 0:
        score += 8
        tags.append("20日非跌")
        conditions.append("trend_20d")
        if 5 <= ret_20d <= 30:
            score += 5
            tags.append("20日强势")

    if ma60 > 0 and price <= ma60 * 1.15:
        score += 3

    if score < MIDTERM_MIN_SCORE:
        return None

    return {
        "score": score,
        "tags": tags,
        "conditions": conditions,
        "price": price,
        "ma5": round(ma5, 2),
        "ma10": round(ma10, 2),
        "ma20": round(ma20, 2),
        "rsi": round(rsi, 1),
        "ret_20d": round(ret_20d, 2),
        "trend": "多头" if ma5 > ma10 > ma20 else "反弹",
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
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        rsi = round(_rsi(close), 1)

        ret_20d = _safe_pct(price, float(close.iloc[-21])) if len(close) >= 21 else 0
        ret_60d = _safe_pct(price, float(close.iloc[-61])) if len(close) >= 61 else 0

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

    def _score_candidate(self, code: str, name: str, spot: Optional[dict] = None) -> Optional[dict]:
        hist = get_stock_hist(code, days=90)
        if hist.empty or len(hist) < 20:
            return None

        hist = hist.sort_values("date").reset_index(drop=True)
        spot_pct = float(spot.get("pct", 0)) if spot else 0.0
        turnover = float(spot.get("turnover", 0)) if spot else 0.0
        market_cap_yi = spot.get("market_cap_yi") if spot else None

        if spot_pct == 0 and "pct_chg" in hist.columns:
            last_pct = hist["pct_chg"].iloc[-1]
            if pd.notna(last_pct):
                spot_pct = float(last_pct)

        tech = _evaluate_midterm_technicals(hist, spot_pct, turnover)
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
            f"{' · '.join(tech['tags'][:4])}；20日{tech['ret_20d']:+.1f}% RSI{tech['rsi']:.0f}；"
            f"{cap_part}{yoy_part}"
        )

        return {
            "code": code,
            "name": name,
            "price": round(tech["price"], 2),
            "market_cap_yi": round(cap, 1) if cap is not None else None,
            "pct_chg": round(spot_pct, 2),
            "turnover": round(turnover, 2),
            "midterm_score": tech["score"],
            "trend": tech["trend"],
            "rsi": tech["rsi"],
            "ret_20d": tech["ret_20d"],
            "ma5": tech["ma5"],
            "ma10": tech["ma10"],
            "ma20": tech["ma20"],
            "pe": round(float(pe), 2) if pe is not None and pd.notna(pe) else None,
            "profit_yoy": round(float(profit_yoy), 2) if profit_yoy is not None and pd.notna(profit_yoy) else None,
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
        prefilter: int = 220,
        show_progress: bool = False,
        industry: Optional[str] = None,
        performance: Optional[str] = None,
    ) -> Tuple[pd.DataFrame, dict]:
        """中线个股推荐（排除已持仓，支持行业/业绩筛选）。返回 (DataFrame, 筛选统计)。"""
        select_stats: dict = {
            "market_total": 0,
            "prefilter_count": 0,
            "scored_pass": 0,
            "scored_fail": 0,
            "excluded_held": 0,
            "filter": {},
            "fallback_used": False,
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
        df = market.copy()
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
        _progress(
            f"  [初筛] 全市场 {len(df)} 只；股价<{self.max_recommend_price}元: {n_price}；"
            f"市值{self.min_recommend_market_cap:.0f}-{self.max_recommend_market_cap:.0f}亿: {n_cap}",
            show_progress,
        )

        def _build_candidates(pct_min: float, pct_max: float, turnover_min: float) -> pd.DataFrame:
            turn_ok = (df["_turnover"] >= turnover_min) | (df["_turnover"] <= 0)
            mask = (
                (df["_pct"] >= pct_min)
                & (df["_pct"] <= pct_max)
                & turn_ok
                & price_ok
                & cap_ok
            )
            pool = df[mask].copy()
            if pool.empty:
                return pool
            cap_rank = pool["_cap_yi"].fillna(self.min_recommend_market_cap)
            pool["_rank"] = (
                pool["_pct"].clip(-5, 8) * 0.3
                + pool["_turnover"].clip(0, 15) * 0.4
                + cap_rank.clip(self.min_recommend_market_cap, self.max_recommend_market_cap)
                / self.max_recommend_market_cap * 0.3
            )
            return pool.sort_values("_rank", ascending=False).head(prefilter)

        candidates = _build_candidates(-3, 8, 0.3)
        if candidates.empty:
            _progress("  初筛偏严，放宽至涨跌幅-5%~9%…", show_progress)
            candidates = _build_candidates(-5, 9, 0.2)
        if candidates.empty and has_cap:
            _progress("  仍无候选，放宽市值至120亿起…", show_progress)
            cap_ok = (
                (df["_cap_yi"] >= max(self.min_recommend_market_cap - 30, 80))
                & (df["_cap_yi"] < self.max_recommend_market_cap)
            )
            candidates = _build_candidates(-5, 9, 0.2)

        select_stats["prefilter_count"] = len(candidates)
        _progress(f"  初筛 {len(candidates)} 只，技术面评分中…", show_progress)

        results: List[dict] = []
        excluded_held = scored_fail = 0
        total = len(candidates)
        for idx, (_, row) in enumerate(candidates.iterrows(), 1):
            if show_progress and (idx == 1 or idx % 15 == 0 or idx == total):
                _progress(f"  评分进度 {idx}/{total}", True)
            code = str(row[code_col]).zfill(6)
            if code in exclude:
                excluded_held += 1
                continue
            name = str(row[name_col]) if name_col else code
            cap_yi = row["_cap_yi"]
            fund = fundamental_map.get(code, {})
            spot = {
                "pct": row["_pct"],
                "turnover": row["_turnover"],
                "market_cap_yi": float(cap_yi) if pd.notna(cap_yi) else None,
                "pe": fund.get("pe"),
                "profit_yoy": fund.get("profit_yoy"),
            }
            item = self._score_candidate(code, name, spot)
            if item:
                results.append(item)
            else:
                scored_fail += 1

        select_stats["scored_pass"] = len(results)
        select_stats["scored_fail"] = scored_fail
        select_stats["excluded_held"] = excluded_held
        _progress(
            f"  [评分] 技术面命中 {len(results)}/{total - excluded_held} 只"
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

        return {
            "generated_at": datetime.now().isoformat(),
            "style": "中线",
            "quick": True,
            "reviews": reviews,
            "optimization": optimization,
            "recommendations": [],
            "suggestions": review_summaries + opt_suggestions,
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
        _progress("[3/4] 全市场推荐扫描", show_progress)
        recommendations, select_stats = self.recommend_stocks(
            exclude_codes=held_codes,
            top_n=20,
            show_progress=show_progress,
            industry=industry,
            performance=performance,
        )

        _progress("[4/4] 生成报告", show_progress)
        review_summaries = [r["summary"] for r in reviews if r.get("ok")]
        opt_suggestions = optimization.get("suggestions", [])
        rec_records = recommendations.to_dict("records") if not recommendations.empty else []

        all_text = review_summaries + opt_suggestions
        for r in rec_records[:5]:
            all_text.append(
                f"【推荐】{r['name']}({r['code']}) 评分{r['midterm_score']} "
                f"{r['reason']} 标签:{r.get('tags', '')}"
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
            "suggestions": all_text,
            "review_summaries": review_summaries,
            "optimize_suggestions": opt_suggestions,
        }

        path = OUTPUT_DIR / f"midterm_{datetime.now().strftime('%Y%m%d')}.json"
        path.write_text(
            json.dumps(
                {
                    **result,
                    "reviews": reviews,
                    "recommendations": rec_records,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        md = format_midterm_report_markdown(result)
        md_path = OUTPUT_DIR / f"midterm_{datetime.now().strftime('%Y%m%d')}.md"
        md_path.write_text(md, encoding="utf-8")
        result["markdown"] = md
        result["report_path"] = str(md_path)
        _progress(f"分析完成：推荐 {len(rec_records)} 只，报告已保存", show_progress)
        return result


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

    if result.get("optimize_suggestions"):
        parts.append("## 二、持仓优化\n\n")
        for i, s in enumerate(result["optimize_suggestions"], 1):
            parts.append(f"{i}. {s}\n")
        parts.append("\n")

    recs = result.get("recommendations", [])
    if recs:
        parts.append("## 三、个股推荐（市值150-1000亿 · 均线多头 · 放量上涨）\n\n")
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
        return json.loads(files[0].read_text(encoding="utf-8"))
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
