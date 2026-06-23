"""
实盘中线顾问
- 个股复盘：趋势、均线、RSI、支撑阻力
- 持仓优化：仓位配比、加减仓建议
- 个股推荐：中线趋势 + 适度动量筛选
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from stock_data import get_market_spot, get_stock_hist, get_stock_code_column, get_stock_name_column

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "midterm"
MIDTERM_STRATEGIES = {"中线", "趋势", "手动", "价值", "波段"}


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


class MidtermPortfolioAdvisor:
    """实盘中线：个股复盘、持仓优化、推荐。"""

    def __init__(
        self,
        max_single_weight: float = 30.0,
        target_position_count: Tuple[int, int] = (3, 6),
    ):
        self.max_single_weight = max_single_weight
        self.target_position_count = target_position_count
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

    def review_holdings(self, positions: List[dict]) -> List[dict]:
        """逐只持仓中线复盘。"""
        reviews = []
        for p in positions:
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

        positions = portfolio_stats["positions"]
        reviews = reviews or self.review_holdings(positions)
        review_map = {r["code"]: r for r in reviews if r.get("ok")}

        total_capital = portfolio_stats["total_capital"]
        invested_pct = portfolio_stats["invested_pct"]
        count = len(positions)
        min_pos, max_pos = self.target_position_count
        ideal_weight = 100 / max(count, min_pos)

        suggestions: List[str] = []
        actions: List[dict] = []

        suggestions.append(
            f"中线组合：{count} 只持仓，仓位 {invested_pct:.0f}%，"
            f"建议持有 {min_pos}-{max_pos} 只、单票≤{self.max_single_weight:.0f}%。"
        )

        if invested_pct > 92:
            suggestions.append("整体接近满仓，中线应保留 15%-25% 现金应对回调与换仓。")
        elif invested_pct < 55 and count < max_pos:
            suggestions.append(
                f"仓位偏轻（{invested_pct:.0f}%），可择优布局 {max_pos - count} 只中线标的。"
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
        if hist.empty or len(hist) < 40:
            return None

        hist = hist.sort_values("date").reset_index(drop=True)
        close = pd.to_numeric(hist["close"], errors="coerce")
        price = float(close.iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else ma20
        rsi = _rsi(close)
        ret_20d = _safe_pct(price, float(close.iloc[-21])) if len(close) >= 21 else 0

        spot_pct = float(spot.get("pct", 0)) if spot else float(hist.get("pct_chg", pd.Series([0])).iloc[-1] or 0)
        turnover = float(spot.get("turnover", 0)) if spot else 0

        if spot_pct >= 9 or spot_pct <= -9:
            return None
        if turnover > 15 or (turnover > 0 and turnover < 0.3):
            return None

        score = 40
        tags: List[str] = []

        if price >= ma20 >= ma60:
            score += 25
            tags.append("均线多头")
        elif price >= ma20:
            score += 12
            tags.append("站上MA20")
        else:
            return None

        if 45 <= rsi <= 62:
            score += 15
            tags.append("RSI适中")
        elif rsi > 70:
            return None

        if 5 <= ret_20d <= 22:
            score += 15
            tags.append("20日强势")
        elif ret_20d < 0:
            return None

        if ma60 > 0 and price <= ma60 * 1.08:
            score += 5

        if score < 70:
            return None

        return {
            "code": code,
            "name": name,
            "price": round(price, 2),
            "pct_chg": round(spot_pct, 2),
            "turnover": round(turnover, 2),
            "midterm_score": score,
            "trend": "多头" if price >= ma20 >= ma60 else "反弹",
            "rsi": round(rsi, 1),
            "ret_20d": round(ret_20d, 2),
            "ma20": round(ma20, 2),
            "tags": ",".join(tags),
            "reason": f"中线趋势良好，20日涨幅 {ret_20d:.1f}%，RSI {rsi:.0f}",
        }

    def recommend_stocks(
        self,
        exclude_codes: Optional[List[str]] = None,
        top_n: int = 8,
        prefilter: int = 150,
        show_progress: bool = False,
    ) -> pd.DataFrame:
        """中线个股推荐（排除已持仓）。"""
        exclude = {str(c).zfill(6) for c in (exclude_codes or [])}
        market = get_market_spot(verbose=show_progress, force_refresh=False)
        if market.empty:
            return pd.DataFrame()

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

        mask = (df["_pct"] >= -2) & (df["_pct"] <= 7) & (df["_turnover"] >= 0.5)
        candidates = df[mask].copy()
        candidates["_rank"] = candidates["_pct"] * 0.3 + candidates["_turnover"] * 0.7
        candidates = candidates.sort_values("_rank", ascending=False).head(prefilter)

        results: List[dict] = []
        for _, row in candidates.iterrows():
            code = str(row[code_col]).zfill(6)
            if code in exclude:
                continue
            name = str(row[name_col]) if name_col else code
            spot = {"pct": row["_pct"], "turnover": row["_turnover"]}
            item = self._score_candidate(code, name, spot)
            if item:
                results.append(item)

        if not results:
            return pd.DataFrame()

        out = pd.DataFrame(results).sort_values("midterm_score", ascending=False).head(top_n)
        return out.reset_index(drop=True)

    def run_quick_advice(self, portfolio_stats: dict) -> dict:
        """轻量分析：仅持仓复盘 + 优化（不扫全市场推荐）。"""
        positions = portfolio_stats.get("positions", [])
        reviews = self.review_holdings(positions)
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

    def run_full_advice(self, portfolio_stats: dict, show_progress: bool = False) -> dict:
        """完整实盘中线分析：复盘 + 优化 + 推荐。"""
        positions = portfolio_stats.get("positions", [])
        reviews = self.review_holdings(positions)
        optimization = self.optimize_positions(portfolio_stats, reviews)
        held_codes = [p["code"] for p in positions]
        recommendations = self.recommend_stocks(
            exclude_codes=held_codes, top_n=8, show_progress=show_progress
        )

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
        return result


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
) -> dict:
    from portfolio import PortfolioManager

    if portfolio_stats is None:
        portfolio_stats = PortfolioManager().analyze()
    advisor = MidtermPortfolioAdvisor()
    if full:
        return advisor.run_full_advice(portfolio_stats, show_progress=show_progress)
    return advisor.run_quick_advice(portfolio_stats)
