"""
个人交易日记与学习分析
记录买卖操作，统计胜率/持仓习惯，生成优化建议。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from quantpy.paths import DATA_DIR, TRADES_FILE


@dataclass
class TradeRecord:
    """一笔完整交易（买入到卖出）。"""

    code: str
    name: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    quantity: int = 100
    strategy: str = "手动"  # 超短 / 涨停 / 趋势 / 手动
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def profit_pct(self) -> float:
        if self.buy_price <= 0:
            return 0.0
        return (self.sell_price - self.buy_price) / self.buy_price * 100

    @property
    def profit_amount(self) -> float:
        return (self.sell_price - self.buy_price) * self.quantity

    @property
    def hold_days(self) -> int:
        try:
            buy = datetime.strptime(self.buy_date[:10], "%Y-%m-%d")
            sell = datetime.strptime(self.sell_date[:10], "%Y-%m-%d")
            return max((sell - buy).days, 0)
        except ValueError:
            return 0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_summary(self) -> dict:
        d = self.to_dict()
        d["profit_pct"] = round(self.profit_pct, 2)
        d["profit_amount"] = round(self.profit_amount, 2)
        d["hold_days"] = self.hold_days
        return d


class TradeJournal:
    """交易日记：增删查 + 绩效分析 + 学习建议。"""

    def __init__(self, path: Path = TRADES_FILE):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._trades: List[TradeRecord] = self._load()

    def _load(self) -> List[TradeRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            fields = set(TradeRecord.__dataclass_fields__.keys())
            trades = []
            for item in raw.get("trades", []):
                payload = {k: v for k, v in item.items() if k in fields}
                trades.append(TradeRecord(**payload))
            return trades
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    def _save(self) -> None:
        payload = {
            "updated_at": datetime.now().isoformat(),
            "trades": [t.to_dict() for t in self._trades],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_trade(
        self,
        code: str,
        name: str,
        buy_date: str,
        buy_price: float,
        sell_date: str,
        sell_price: float,
        quantity: int = 100,
        strategy: str = "手动",
        note: str = "",
    ) -> TradeRecord:
        record = TradeRecord(
            code=str(code).zfill(6),
            name=name,
            buy_date=buy_date,
            buy_price=float(buy_price),
            sell_date=sell_date,
            sell_price=float(sell_price),
            quantity=int(quantity),
            strategy=strategy,
            note=note,
        )
        self._trades.append(record)
        self._save()
        return record

    def list_trades(self, days: Optional[int] = None) -> pd.DataFrame:
        if not self._trades:
            return pd.DataFrame()
        rows = [t.to_summary() for t in self._trades]
        df = pd.DataFrame(rows)
        if days is not None and "sell_date" in df.columns:
            cutoff = (datetime.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
            df = df[df["sell_date"] >= cutoff]
        return df.sort_values("sell_date", ascending=False)

    def analyze(self, days: int = 30) -> Dict:
        df = self.list_trades(days=days)
        if df.empty:
            return {
                "period_days": days,
                "trade_count": 0,
                "has_data": False,
            }

        wins = df[df["profit_pct"] > 0]
        losses = df[df["profit_pct"] <= 0]

        by_strategy = (
            df.groupby("strategy")
            .agg(
                count=("profit_pct", "count"),
                win_rate=("profit_pct", lambda s: (s > 0).mean() * 100),
                avg_profit=("profit_pct", "mean"),
                avg_hold=("hold_days", "mean"),
            )
            .round(2)
            .reset_index()
        )

        return {
            "has_data": True,
            "period_days": days,
            "trade_count": len(df),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(df) * 100, 1),
            "avg_profit_pct": round(df["profit_pct"].mean(), 2),
            "total_profit_amount": round(df["profit_amount"].sum(), 2),
            "avg_hold_days": round(df["hold_days"].mean(), 1),
            "max_profit_pct": round(df["profit_pct"].max(), 2),
            "max_loss_pct": round(df["profit_pct"].min(), 2),
            "best_trade": df.loc[df["profit_pct"].idxmax()].to_dict(),
            "worst_trade": df.loc[df["profit_pct"].idxmin()].to_dict(),
            "by_strategy": by_strategy.to_dict("records"),
            "recent_codes": df["code"].tolist()[:10],
        }

    def generate_suggestions(self, days: int = 30) -> List[str]:
        """根据历史操作生成个性化优化建议。"""
        stats = self.analyze(days=days)
        suggestions: List[str] = []

        if not stats.get("has_data"):
            suggestions.append(
                "尚未记录交易。使用 `python daily_advisor.py record` 录入买卖，"
                "系统将据此学习你的操作习惯并给出建议。"
            )
            suggestions.append(
                "建议每笔超短交易记录：代码、买卖价、日期、策略标签（超短/涨停/趋势）。"
            )
            return suggestions

        win_rate = stats["win_rate"]
        avg_hold = stats["avg_hold_days"]
        avg_profit = stats["avg_profit_pct"]

        if win_rate < 45:
            suggestions.append(
                f"近{days}日胜率 {win_rate}%，偏低。建议：缩小单笔仓位，"
                "设置 -3% 硬止损，避免亏损加仓。"
            )
        elif win_rate >= 60:
            suggestions.append(
                f"近{days}日胜率 {win_rate}%，表现不错。可适度提高仓位，"
                "但需严格执行止盈，避免利润回吐。"
            )

        if avg_profit < 0:
            suggestions.append(
                f"近{days}日平均每笔收益 {avg_profit}%，整体亏损。"
                "建议暂停追涨，复盘最差交易（见报告），检查是否追高被套。"
            )
        elif avg_profit > 3:
            suggestions.append(
                f"平均每笔盈利 {avg_profit}%，保持当前节奏，"
                "注意勿因连续盈利而放大风险敞口。"
            )

        ultra_short = [s for s in stats.get("by_strategy", []) if s.get("strategy") == "超短"]
        if ultra_short:
            us = ultra_short[0]
            if us["avg_hold"] > 3:
                suggestions.append(
                    f"超短策略平均持仓 {us['avg_hold']} 天，偏长。"
                    "超短宜 1-3 日了结，隔夜需评估题材强度与封板质量。"
                )
            if us["win_rate"] < 50:
                suggestions.append(
                    f"超短策略胜率 {us['win_rate']}%，建议只做放量涨停回封、"
                    "或涨停后不破开盘价的强势票（参见今日超短扫描结果）。"
                )

        limit_up = [s for s in stats.get("by_strategy", []) if s.get("strategy") == "涨停"]
        if limit_up and limit_up[0]["win_rate"] < 45:
            suggestions.append(
                "涨停追击胜率不高。优化：① 不打板，等分歧回封；"
                "② 优先连板高度 2-3 板且换手充分的龙头；③ 弱势行情减少出手。"
            )

        if avg_hold > 5 and not ultra_short:
            suggestions.append(
                f"平均持仓 {avg_hold} 天，偏中线。若目标为超短，"
                "建议将持仓压缩至 3 日以内，提高资金周转率。"
            )

        worst = stats.get("worst_trade", {})
        if worst and worst.get("profit_pct", 0) < -5:
            suggestions.append(
                f"最大单笔亏损 {worst.get('name')}({worst.get('code')}) "
                f"{worst.get('profit_pct')}%。复盘：买入逻辑是否成立、"
                "是否违反止损纪律。"
            )

        if len(suggestions) < 2:
            suggestions.append(
                "继续保持交易记录，样本越多建议越精准。"
                "每日运行 `python daily_advisor.py` 获取超短机会与复盘建议。"
            )

        return suggestions

    def import_from_csv(self, csv_path: str) -> int:
        """从 CSV 批量导入。列：code,name,buy_date,buy_price,sell_date,sell_price,quantity,strategy,note"""
        df = pd.read_csv(csv_path, dtype={"code": str})
        count = 0
        for _, row in df.iterrows():
            self.add_trade(
                code=str(row["code"]).zfill(6),
                name=str(row.get("name", "")),
                buy_date=str(row["buy_date"]),
                buy_price=float(row["buy_price"]),
                sell_date=str(row["sell_date"]),
                sell_price=float(row["sell_price"]),
                quantity=int(row.get("quantity", 100)),
                strategy=str(row.get("strategy", "手动")),
                note=str(row.get("note", "")),
            )
            count += 1
        return count


def interactive_record() -> None:
    """命令行交互录入一笔交易。"""
    print("=" * 50)
    print("录入交易记录")
    print("=" * 50)
    journal = TradeJournal()

    code = input("股票代码 (如 600519): ").strip()
    name = input("股票名称: ").strip()
    buy_date = input("买入日期 (YYYY-MM-DD): ").strip()
    buy_price = float(input("买入价格: ").strip())
    sell_date = input("卖出日期 (YYYY-MM-DD): ").strip()
    sell_price = float(input("卖出价格: ").strip())
    quantity = input("数量 (默认100): ").strip() or "100"
    strategy = input("策略标签 [超短/涨停/趋势/手动] (默认手动): ").strip() or "手动"
    note = input("备注 (可选): ").strip()

    record = journal.add_trade(
        code=code,
        name=name,
        buy_date=buy_date,
        buy_price=buy_price,
        sell_date=sell_date,
        sell_price=sell_price,
        quantity=int(quantity),
        strategy=strategy,
        note=note,
    )
    print(f"\n已保存: {record.name}({record.code}) 收益 {record.profit_pct:.2f}%")


if __name__ == "__main__":
    interactive_record()
