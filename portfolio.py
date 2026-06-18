"""
个人仓位管理
记录总资金、当前持仓，并结合行情计算市值、浮盈亏、仓位占比。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from stock_data import get_latest_price, get_market_spot, get_realtime_quotes

DATA_DIR = Path(__file__).resolve().parent / "data"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"


@dataclass
class Position:
    code: str
    name: str
    quantity: int
    cost_price: float
    buy_date: str = ""
    strategy: str = "手动"
    note: str = ""

    @property
    def cost_amount(self) -> float:
        return self.quantity * self.cost_price


@dataclass
class Portfolio:
    total_capital: float = 170000.0
    positions: List[Position] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class PortfolioManager:
    def __init__(self, path: Path = PORTFOLIO_FILE):
        self.path = path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._portfolio = self._load()

    def _load(self) -> Portfolio:
        if not self.path.exists():
            return Portfolio()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            positions = [
                Position(**{k: v for k, v in p.items() if k in Position.__dataclass_fields__})
                for p in raw.get("positions", [])
            ]
            return Portfolio(
                total_capital=float(raw.get("total_capital", 170000)),
                positions=positions,
                updated_at=raw.get("updated_at", datetime.now().isoformat()),
            )
        except (json.JSONDecodeError, TypeError, KeyError):
            return Portfolio()

    def save(self) -> None:
        self._portfolio.updated_at = datetime.now().isoformat()
        payload = {
            "total_capital": self._portfolio.total_capital,
            "updated_at": self._portfolio.updated_at,
            "positions": [asdict(p) for p in self._portfolio.positions],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_total_capital(self, amount: float) -> None:
        self._portfolio.total_capital = float(amount)
        self.save()

    def set_positions(self, positions: List[Position]) -> None:
        self._portfolio.positions = positions
        self.save()

    def upsert_position(
        self,
        code: str,
        name: str,
        quantity: int,
        cost_price: float,
        buy_date: str = "",
        strategy: str = "手动",
        note: str = "",
    ) -> None:
        code = str(code).zfill(6)
        for i, p in enumerate(self._portfolio.positions):
            if p.code == code:
                self._portfolio.positions[i] = Position(
                    code=code,
                    name=name,
                    quantity=int(quantity),
                    cost_price=float(cost_price),
                    buy_date=buy_date,
                    strategy=strategy,
                    note=note,
                )
                self.save()
                return
        self._portfolio.positions.append(
            Position(code, name, int(quantity), float(cost_price), buy_date, strategy, note)
        )
        self.save()

    def remove_position(self, code: str) -> bool:
        code = str(code).zfill(6)
        before = len(self._portfolio.positions)
        self._portfolio.positions = [p for p in self._portfolio.positions if p.code != code]
        if len(self._portfolio.positions) < before:
            self.save()
            return True
        return False

    def list_positions(self) -> List[Position]:
        return list(self._portfolio.positions)

    def _fetch_price(self, code: str, quote_map: Optional[pd.DataFrame] = None) -> float:
        code = str(code).zfill(6)
        if quote_map is not None and not quote_map.empty:
            row = quote_map[quote_map["code"] == code]
            if not row.empty:
                for col in ("close", "price"):
                    if col in row.columns:
                        val = pd.to_numeric(row.iloc[0][col], errors="coerce")
                        if pd.notna(val) and val > 0:
                            return float(val)
        price = get_latest_price(code)
        return price if price > 0 else 0.0

    def analyze(self, spot_df: Optional[pd.DataFrame] = None) -> Dict:
        codes = [p.code for p in self._portfolio.positions]
        quote_map = get_realtime_quotes(codes) if codes else pd.DataFrame()
        if spot_df is None:
            spot_df = quote_map

        rows = []
        total_market_value = 0.0
        total_cost = 0.0

        for pos in self._portfolio.positions:
            price = self._fetch_price(pos.code, quote_map)
            market_value = price * pos.quantity
            cost_amount = pos.cost_amount
            profit_amount = market_value - cost_amount
            profit_pct = (price - pos.cost_price) / pos.cost_price * 100 if pos.cost_price > 0 else 0

            rows.append({
                "code": pos.code,
                "name": pos.name,
                "quantity": pos.quantity,
                "cost_price": pos.cost_price,
                "current_price": round(price, 2),
                "cost_amount": round(cost_amount, 2),
                "market_value": round(market_value, 2),
                "profit_amount": round(profit_amount, 2),
                "profit_pct": round(profit_pct, 2),
                "weight_pct": 0.0,
                "strategy": pos.strategy,
                "note": pos.note,
            })
            total_market_value += market_value
            total_cost += cost_amount

        total_capital = self._portfolio.total_capital
        cash = max(total_capital - total_cost, 0)
        equity = cash + total_market_value
        total_float_pnl = total_market_value - total_cost

        for row in rows:
            row["weight_pct"] = round(
                row["market_value"] / total_capital * 100 if total_capital > 0 else 0, 2
            )

        invested_pct = round(total_cost / total_capital * 100, 2) if total_capital > 0 else 0
        position_count = len(rows)

        return {
            "has_data": position_count > 0,
            "total_capital": total_capital,
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "cash_estimated": round(cash, 2),
            "equity_estimated": round(equity, 2),
            "total_float_pnl": round(total_float_pnl, 2),
            "total_float_pnl_pct": round(total_float_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
            "invested_pct": invested_pct,
            "position_count": position_count,
            "positions": rows,
            "quote_time": str(quote_map["quote_time"].iloc[0]) if not quote_map.empty and "quote_time" in quote_map.columns else "",
            "trade_date": str(quote_map["trade_date"].iloc[0]) if not quote_map.empty and "trade_date" in quote_map.columns else "",
            "updated_at": self._portfolio.updated_at,
        }

    def generate_suggestions(self, spot_df: Optional[pd.DataFrame] = None) -> List[str]:
        stats = self.analyze(spot_df)
        suggestions: List[str] = []

        if not stats.get("has_data"):
            suggestions.append("尚未录入持仓。使用 `python daily_advisor.py portfolio` 查看或编辑。")
            return suggestions

        total_capital = stats["total_capital"]
        invested_pct = stats["invested_pct"]
        positions = stats["positions"]

        suggestions.append(
            f"总资金 {total_capital/10000:.1f} 万，已投入 {stats['total_cost']:.0f} 元"
            f"（仓位 {invested_pct}%），浮盈 {stats['total_float_pnl']:+.0f} 元"
            f"（{stats['total_float_pnl_pct']:+.2f}%）。"
        )

        if invested_pct > 95:
            suggestions.append("仓位接近满仓，建议保留 10%-20% 现金应对波动或捕捉新机会。")
        elif invested_pct < 50:
            suggestions.append(f"当前仅使用 {invested_pct}% 资金，若看好市场可逐步加仓优质标的。")

        for p in positions:
            if p["profit_pct"] <= -8:
                suggestions.append(
                    f"{p['name']}({p['code']}) 浮亏 {p['profit_pct']:.1f}%，"
                    f"占仓 {p['weight_pct']:.1f}%。评估买入逻辑，考虑 -5%~-8% 止损线。"
                )
            elif p["profit_pct"] >= 15:
                suggestions.append(
                    f"{p['name']}({p['code']}) 浮盈 {p['profit_pct']:.1f}%，"
                    "可考虑分批止盈，锁定部分利润。"
                )

        max_weight = max(positions, key=lambda x: x["weight_pct"])
        if max_weight["weight_pct"] > 40:
            suggestions.append(
                f"{max_weight['name']} 单票占比 {max_weight['weight_pct']:.1f}% 偏高，"
                "建议单票控制在 30% 以内以分散风险。"
            )

        losers = [p for p in positions if p["profit_pct"] < -3]
        winners = [p for p in positions if p["profit_pct"] > 3]
        if losers and winners:
            suggestions.append(
                f"持仓分化明显：盈利 {len(winners)} 只、亏损 {len(losers)} 只。"
                "弱势股勿轻易补仓，优先处理逻辑破位的仓位。"
            )

        return suggestions

    def print_summary(self, spot_df: Optional[pd.DataFrame] = None) -> Dict:
        stats = self.analyze(spot_df)
        print("=" * 60)
        print("个人仓位")
        print("=" * 60)
        print(f"总资金:     {stats['total_capital']:,.0f} 元 ({stats['total_capital']/10000:.1f} 万)")
        print(f"持仓成本:   {stats['total_cost']:,.0f} 元 ({stats['invested_pct']:.1f}%)")
        print(f"持仓市值:   {stats['total_market_value']:,.0f} 元")
        print(f"预估现金:   {stats['cash_estimated']:,.0f} 元")
        print(f"账户权益:   {stats['equity_estimated']:,.0f} 元")
        print(f"浮动盈亏:   {stats['total_float_pnl']:+,.0f} 元 ({stats['total_float_pnl_pct']:+.2f}%)")
        if stats.get("trade_date"):
            print(f"行情日期:   {stats['trade_date']}  (腾讯实时)")
        print("-" * 60)

        if stats["positions"]:
            df = pd.DataFrame(stats["positions"])
            cols = ["code", "name", "quantity", "cost_price", "current_price",
                    "profit_pct", "market_value", "weight_pct"]
            print(df[cols].to_string(index=False))
        else:
            print("（暂无持仓）")
        print("=" * 60)
        return stats


def init_default_portfolio() -> PortfolioManager:
    """初始化用户默认仓位（总资金 17 万 + 三只持仓）。"""
    pm = PortfolioManager()
    pm.set_total_capital(170000)
    pm.set_positions([
        Position("603379", "三美股份", 300, 66.5),
        # 成本 42.2 与 002472 双环传动现价吻合（非 000707 双环科技）
        Position("002472", "双环传动", 1200, 42.2, note="用户称双环科技，代码002472"),
        Position("603606", "东方电缆", 500, 41.3),
    ])
    return pm


if __name__ == "__main__":
    pm = PortfolioManager()
    if not pm.list_positions():
        pm = init_default_portfolio()
        print("已写入默认仓位到 data/portfolio.json")
    pm.print_summary()
