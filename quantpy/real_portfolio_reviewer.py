"""
实盘操作复盘：历史买卖点分析 + 优化建议报告
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd

from quantpy.paths import REAL_REVIEW_DIR
from quantpy.portfolio import PortfolioManager, ULTRA_SHORT_STRATEGIES, bucket_label, classify_bucket
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import get_stock_hist
from quantpy.trade_journal import TradeJournal


def _hold_days(buy_date: str, sell_date: str) -> int:
    try:
        buy = datetime.strptime(str(buy_date)[:10], "%Y-%m-%d")
        sell = datetime.strptime(str(sell_date)[:10], "%Y-%m-%d")
        return max((sell - buy).days, 0)
    except ValueError:
        return 0


def _trade_key(row: dict) -> tuple:
    return (
        str(row.get("code", "")).zfill(6),
        str(row.get("sell_date", ""))[:10],
        int(row.get("quantity", 0)),
        round(float(row.get("sell_price", 0)), 4),
    )


class RealPortfolioReviewer:
    """实盘持仓操作复盘：清盘记录 + 交易日记 → 买卖点评估与优化建议。"""

    def __init__(self, days: int = 90):
        self.days = days
        REAL_REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    def _collect_trades(self) -> pd.DataFrame:
        pm = PortfolioManager()
        stats = pm.analyze()
        rows: List[dict] = []
        seen: set[tuple] = set()

        for c in stats.get("closed_positions", []):
            key = _trade_key(c)
            if key in seen:
                continue
            seen.add(key)
            buy_p = float(c.get("cost_price", 0))
            sell_p = float(c.get("sell_price", 0))
            qty = int(c.get("quantity", 0))
            profit_amount = float(c.get("profit_amount", (sell_p - buy_p) * qty))
            profit_pct = float(
                c.get("profit_pct", (sell_p - buy_p) / buy_p * 100 if buy_p else 0)
            )
            rows.append(
                {
                    "code": key[0],
                    "name": c.get("name", key[0]),
                    "buy_date": c.get("buy_date", ""),
                    "sell_date": c.get("sell_date", ""),
                    "buy_price": buy_p,
                    "sell_price": sell_p,
                    "quantity": qty,
                    "strategy": c.get("strategy", "手动"),
                    "bucket": c.get("bucket", classify_bucket(c.get("strategy", ""))),
                    "profit_pct": round(profit_pct, 2),
                    "profit_amount": round(profit_amount, 2),
                    "hold_days": _hold_days(c.get("buy_date", ""), c.get("sell_date", "")),
                    "source": "portfolio",
                }
            )

        journal = TradeJournal()
        jdf = journal.list_trades(days=self.days)
        if not jdf.empty:
            for _, r in jdf.iterrows():
                item = r.to_dict()
                key = _trade_key(item)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    {
                        "code": key[0],
                        "name": item.get("name", key[0]),
                        "buy_date": item.get("buy_date", ""),
                        "sell_date": item.get("sell_date", ""),
                        "buy_price": float(item.get("buy_price", 0)),
                        "sell_price": float(item.get("sell_price", 0)),
                        "quantity": int(item.get("quantity", 0)),
                        "strategy": item.get("strategy", "手动"),
                        "bucket": classify_bucket(item.get("strategy", "")),
                        "profit_pct": float(item.get("profit_pct", 0)),
                        "profit_amount": float(item.get("profit_amount", 0)),
                        "hold_days": int(item.get("hold_days", 0)),
                        "source": "journal",
                    }
                )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        cutoff = (datetime.now() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        df = df[df["sell_date"].astype(str) >= cutoff].sort_values("sell_date", ascending=False)
        return df.reset_index(drop=True)

    def _analyze_timing(self, row: pd.Series, hist: pd.DataFrame) -> dict:
        issues: List[str] = []
        buy_timing = "合理"
        sell_timing = "合理"
        score = 70.0
        profit_pct = float(row["profit_pct"])
        strategy = str(row["strategy"])
        buy_price = float(row["buy_price"])
        sell_price = float(row["sell_price"])

        if hist.empty or len(hist) < 10:
            comment = "K线不足，仅按盈亏与持仓周期评估。"
            if profit_pct < 0:
                issues.append("亏损出局，复盘买入逻辑与止损点")
                score -= 10
            return {
                "buy_timing": buy_timing,
                "sell_timing": sell_timing,
                "timing_score": round(score, 0),
                "issues": issues,
                "comment": comment,
            }

        hist = hist.copy()
        hist["date"] = pd.to_datetime(hist["date"])
        buy_dt = pd.to_datetime(str(row["buy_date"])[:10])
        sell_dt = pd.to_datetime(str(row["sell_date"])[:10])

        before_buy = hist[hist["date"] <= buy_dt]
        if len(before_buy) >= 20:
            close = pd.to_numeric(before_buy["close"], errors="coerce")
            ma20 = float(close.rolling(20).mean().iloc[-1])
            if ma20 > 0:
                ratio = buy_price / ma20
                if ratio > 1.08:
                    buy_timing = "偏高"
                    issues.append("买入价明显高于MA20，存在追高")
                    score -= 12
                elif ratio < 0.95:
                    buy_timing = "低位"
                    score += 5

        hold = hist[(hist["date"] >= buy_dt) & (hist["date"] <= sell_dt)]
        if not hold.empty and profit_pct > 0:
            peak = float(pd.to_numeric(hold["high"], errors="coerce").max())
            if peak > 0 and sell_price < peak * 0.9:
                sell_timing = "偏早"
                gap = (1 - sell_price / peak) * 100
                issues.append(f"卖出价低于持仓期高点约 {gap:.1f}%，可能卖早")
                score -= 6

        hold_days = int(row["hold_days"])
        if strategy in ULTRA_SHORT_STRATEGIES:
            if hold_days > 3:
                issues.append(f"超短策略持仓 {hold_days} 天偏长，宜 1-3 日")
                score -= 10
            if profit_pct < -3:
                issues.append("超短亏损超3%，需更严止损或减少追高")
                score -= 8
        else:
            if hold_days > 30:
                issues.append(f"中线持仓 {hold_days} 天偏久，关注趋势是否钝化")
                score -= 5

        if profit_pct <= -8:
            issues.append(f"单笔亏损 {profit_pct:.1f}% 偏大，检查是否未执行止损")
            score -= 15
        elif profit_pct >= 12:
            score += 8
            issues.append("盈利可观，可总结可复制买点特征")

        if not issues:
            issues.append("买卖节奏整体尚可，保持纪律")

        comment = (
            f"买入{buy_timing}，卖出{sell_timing}；"
            f"持仓{hold_days}日，收益{profit_pct:+.2f}%。"
            + issues[0]
        )
        return {
            "buy_timing": buy_timing,
            "sell_timing": sell_timing,
            "timing_score": round(max(0, min(100, score)), 0),
            "issues": issues,
            "comment": comment,
        }

    def _build_suggestions(self, df: pd.DataFrame, trade_reviews: List[dict]) -> List[str]:
        if df.empty:
            return [
                "暂无清盘/交易记录。通过「交易录入」卖出并填写卖出价，或录入完整买卖日记。",
                "建议每笔实盘平仓记录：代码、买卖价、日期、策略（超短/中线/ETF）。",
            ]

        suggestions: List[str] = []
        win_rate = (df["profit_pct"] > 0).mean() * 100
        avg_profit = df["profit_pct"].mean()
        total_amt = df["profit_amount"].sum()
        avg_hold = df["hold_days"].mean()

        suggestions.append(
            f"近{self.days}日共 {len(df)} 笔平仓，胜率 {win_rate:.1f}%，"
            f"合计 {total_amt:+.0f} 元，均收益 {avg_profit:+.2f}%，均持仓 {avg_hold:.1f} 天。"
        )

        high_buy = sum(1 for t in trade_reviews if t.get("buy_timing") == "偏高")
        if high_buy >= max(2, len(trade_reviews) * 0.4):
            suggestions.append(
                f"{high_buy} 笔买入价偏高（超MA20），建议等回调至均线附近再介入，减少追高。"
            )

        early_sell = sum(1 for t in trade_reviews if t.get("sell_timing") == "偏早")
        if early_sell >= 2:
            suggestions.append(
                f"{early_sell} 笔盈利卖偏早，可考虑分批止盈：第一目标 +5%~8% 减仓，余仓跟踪均线。"
            )

        ultra = df[df["strategy"].isin(ULTRA_SHORT_STRATEGIES)]
        if not ultra.empty:
            u_win = (ultra["profit_pct"] > 0).mean() * 100
            u_hold = ultra["hold_days"].mean()
            if u_win < 50:
                suggestions.append(
                    f"超短胜率 {u_win:.0f}% 偏低，优先做强势封板/涨停不破开，弱势行情减少出手。"
                )
            if u_hold > 3:
                suggestions.append(f"超短均持仓 {u_hold:.1f} 天，建议压缩至 3 日内了结。")

        mid = df[~df["strategy"].isin(ULTRA_SHORT_STRATEGIES)]
        if not mid.empty:
            m_loss = mid[mid["profit_pct"] < -5]
            if len(m_loss) >= 2:
                suggestions.append(
                    f"中线有 {len(m_loss)} 笔亏损超5%，破位标的宜果断减仓，勿摊薄成本。"
                )

        worst = df.loc[df["profit_pct"].idxmin()]
        if float(worst["profit_pct"]) < -5:
            suggestions.append(
                f"最大亏损：{worst['name']}({worst['code']}) {worst['profit_pct']:+.2f}%，"
                f"重点复盘该笔买入理由与止损执行情况。"
            )

        best = df.loc[df["profit_pct"].idxmax()]
        if float(best["profit_pct"]) > 5:
            suggestions.append(
                f"最佳交易：{best['name']}({best['code']}) {best['profit_pct']:+.2f}%，"
                f"可复用其买点模式（趋势/仓位/时机）。"
            )

        low_score = [t for t in trade_reviews if t.get("timing_score", 100) < 55]
        if low_score:
            suggestions.append(
                f"{len(low_score)} 笔操作评分偏低，详见逐笔复盘表，优先改进买卖节奏。"
            )

        return suggestions

    def _build_markdown(self, result: dict) -> str:
        lines = [
            "# 实盘操作复盘报告\n",
            f"**生成时间**: {result['generated_at'][:19].replace('T', ' ')}\n",
            f"**统计区间**: 近 {result['period_days']} 日\n\n",
            "## 一、总体绩效\n\n",
        ]
        s = result["summary"]
        lines.append(
            f"- 平仓笔数: {s['trade_count']} | 胜率: {s['win_rate']}% | "
            f"合计盈亏: {s['total_profit_amount']:+.0f} 元\n"
            f"- 均收益: {s['avg_profit_pct']:+.2f}% | 均持仓: {s['avg_hold_days']} 天 | "
            f"平均操作评分: {s.get('avg_timing_score', 0)}\n"
        )

        if result.get("by_strategy"):
            lines.append("\n## 二、按策略统计\n\n")
            strat_rows = [
                [
                    r["strategy"],
                    r["count"],
                    f"{r['win_rate']:.1f}",
                    f"{r['avg_profit']:+.2f}",
                    f"{r['avg_hold']:.1f}",
                    f"{r['total_amount']:+.0f}",
                ]
                for r in result["by_strategy"]
            ]
            lines.append(
                format_markdown_table(
                    ["策略", "笔数", "胜率%", "均收益%", "均持仓天", "合计盈亏"],
                    strat_rows,
                    aligns=["left", "right", "right", "right", "right", "right"],
                )
            )

        reviews = result.get("trade_reviews", [])
        if reviews:
            lines.append("\n## 三、逐笔买卖点复盘\n\n")
            trade_rows = [
                [
                    t["sell_date"],
                    t["code"],
                    truncate_display(t["name"], 8),
                    t.get("bucket_label", "中线"),
                    f"{t['buy_price']:.3f}",
                    f"{t['sell_price']:.3f}",
                    f"{t['profit_pct']:+.2f}",
                    str(t["hold_days"]),
                    t.get("buy_timing", ""),
                    t.get("sell_timing", ""),
                    str(t.get("timing_score", "")),
                ]
                for t in reviews[:20]
            ]
            lines.append(
                format_markdown_table(
                    ["卖出日", "代码", "名称", "账户", "买入", "卖出", "收益%", "天数", "买点", "卖点", "评分"],
                    trade_rows,
                    aligns=["left", "left", "left", "left", "right", "right", "right", "right", "left", "left", "right"],
                )
            )
            lines.append("\n### 重点点评\n\n")
            for t in reviews[:8]:
                lines.append(f"- **{t['name']}({t['code']})** {t['sell_date']}：{t.get('comment', '')}\n")

        lines.append("\n## 四、优化建议\n\n")
        for i, sug in enumerate(result.get("optimization_suggestions", []), 1):
            lines.append(f"{i}. {sug}\n")

        return "".join(lines)

    def run_review(self, show_progress: bool = False) -> dict:
        df = self._collect_trades()
        if df.empty:
            return {
                "ok": True,
                "has_data": False,
                "period_days": self.days,
                "generated_at": datetime.now().isoformat(),
                "summary": {"trade_count": 0},
                "trade_reviews": [],
                "optimization_suggestions": self._build_suggestions(df, []),
                "markdown": "# 实盘操作复盘\n\n暂无平仓记录。\n",
            }

        hist_cache: Dict[str, pd.DataFrame] = {}
        trade_reviews: List[dict] = []

        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            if code not in hist_cache:
                if show_progress:
                    print(f"  分析 {code} K线…")
                hist_cache[code] = get_stock_hist(code, days=120)
            timing = self._analyze_timing(row, hist_cache[code])
            item = row.to_dict()
            item["bucket_label"] = bucket_label(item.get("bucket", "midterm"))
            item.update(timing)
            trade_reviews.append(item)

        wins = df[df["profit_pct"] > 0]
        timing_scores = [t.get("timing_score", 0) for t in trade_reviews]

        by_strategy = (
            df.groupby("strategy")
            .agg(
                count=("profit_pct", "count"),
                win_rate=("profit_pct", lambda s: (s > 0).mean() * 100),
                avg_profit=("profit_pct", "mean"),
                avg_hold=("hold_days", "mean"),
                total_amount=("profit_amount", "sum"),
            )
            .round(2)
            .reset_index()
            .to_dict("records")
        )

        suggestions = self._build_suggestions(df, trade_reviews)
        result = {
            "ok": True,
            "has_data": True,
            "period_days": self.days,
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "trade_count": len(df),
                "win_count": len(wins),
                "loss_count": len(df) - len(wins),
                "win_rate": round(len(wins) / len(df) * 100, 1),
                "avg_profit_pct": round(df["profit_pct"].mean(), 2),
                "total_profit_amount": round(df["profit_amount"].sum(), 2),
                "avg_hold_days": round(df["hold_days"].mean(), 1),
                "avg_timing_score": round(sum(timing_scores) / len(timing_scores), 1) if timing_scores else 0,
                "max_profit_pct": round(df["profit_pct"].max(), 2),
                "max_loss_pct": round(df["profit_pct"].min(), 2),
            },
            "by_strategy": by_strategy,
            "trade_reviews": trade_reviews,
            "optimization_suggestions": suggestions,
        }
        result["markdown"] = self._build_markdown(result)
        return result

    def save_review(self, result: dict) -> Path:
        day = datetime.now().strftime("%Y%m%d")
        path = REAL_REVIEW_DIR / f"real_review_{day}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path = REAL_REVIEW_DIR / f"real_review_{day}.md"
        md_path.write_text(result.get("markdown", ""), encoding="utf-8")
        return path


def run_real_portfolio_review(days: int = 90, show_progress: bool = False) -> dict:
    reviewer = RealPortfolioReviewer(days=days)
    result = reviewer.run_review(show_progress=show_progress)
    if result.get("has_data") or result.get("optimization_suggestions"):
        reviewer.save_review(result)
    return result


def load_latest_real_review() -> dict:
    files = sorted(REAL_REVIEW_DIR.glob("real_review_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
