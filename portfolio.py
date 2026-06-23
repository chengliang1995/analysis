"""
个人仓位管理
记录总资金、当前持仓，并结合行情计算市值、浮盈亏、仓位占比。

持仓与总资金配置见 data/portfolio_config.json（编辑后运行 portfolio --init 同步）。
运行时数据保存在 data/portfolio.json（git 忽略）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd

from stock_data import get_latest_price, get_realtime_quotes
from midterm_portfolio_advisor import MidtermPortfolioAdvisor
from ultra_short_scanner import UltraShortScanner

DATA_DIR = Path(__file__).resolve().parent / "data"
PORTFOLIO_CONFIG_FILE = DATA_DIR / "portfolio_config.json"
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
    total_capital: float = 200000.0
    positions: List[Position] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


def load_portfolio_config(path: Path = PORTFOLIO_CONFIG_FILE) -> dict:
    """读取持仓配置文件。"""
    if not path.exists():
        return {"total_capital": 200000.0, "positions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_positions(raw_positions: list) -> List[Position]:
    positions = []
    for p in raw_positions:
        positions.append(
            Position(**{k: v for k, v in p.items() if k in Position.__dataclass_fields__})
        )
    return positions


def portfolio_from_dict(raw: dict) -> Portfolio:
    return Portfolio(
        total_capital=float(raw.get("total_capital", 200000)),
        positions=_parse_positions(raw.get("positions", [])),
        updated_at=raw.get("updated_at", datetime.now().isoformat()),
    )


class PortfolioManager:
    def __init__(
        self,
        path: Path = PORTFOLIO_FILE,
        config_path: Path = PORTFOLIO_CONFIG_FILE,
    ):
        self.path = path
        self.config_path = config_path
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._portfolio = self._load()

    def _load(self) -> Portfolio:
        if self.path.exists():
            try:
                return portfolio_from_dict(json.loads(self.path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        if self.config_path.exists():
            portfolio = portfolio_from_dict(load_portfolio_config(self.config_path))
            self._portfolio = portfolio
            self.save()
            return portfolio

        return Portfolio()

    def save(self) -> None:
        self._portfolio.updated_at = datetime.now().isoformat()
        payload = {
            "total_capital": self._portfolio.total_capital,
            "updated_at": self._portfolio.updated_at,
            "positions": [asdict(p) for p in self._portfolio.positions],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_to_config(self) -> None:
        """同步写入 portfolio.json 与 portfolio_config.json。"""
        self.save()
        config_payload = {
            "total_capital": int(self._portfolio.total_capital)
            if self._portfolio.total_capital == int(self._portfolio.total_capital)
            else self._portfolio.total_capital,
            "positions": [asdict(p) for p in self._portfolio.positions],
        }
        self.config_path.write_text(
            json.dumps(config_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def apply_config(self, config: Optional[Union[dict, Path]] = None) -> None:
        """从配置文件或字典加载并写入 portfolio.json。"""
        if config is None:
            raw = load_portfolio_config(self.config_path)
        elif isinstance(config, Path):
            raw = load_portfolio_config(config)
        else:
            raw = config
        self._portfolio = portfolio_from_dict(raw)
        self.save_to_config()

    def set_total_capital(self, amount: float) -> None:
        self._portfolio.total_capital = float(amount)
        self.save_to_config()

    def set_positions(self, positions: List[Position]) -> None:
        self._portfolio.positions = positions
        self.save_to_config()

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
                self.save_to_config()
                return
        self._portfolio.positions.append(
            Position(code, name, int(quantity), float(cost_price), buy_date, strategy, note)
        )
        self.save_to_config()

    def remove_position(self, code: str) -> bool:
        code = str(code).zfill(6)
        before = len(self._portfolio.positions)
        self._portfolio.positions = [p for p in self._portfolio.positions if p.code != code]
        if len(self._portfolio.positions) < before:
            self.save_to_config()
            return True
        return False

    def apply_sell(self, code: str, quantity: int) -> tuple[bool, str]:
        """交易卖出后扣减实盘持仓，数量归零则移除。"""
        code = str(code).zfill(6)
        quantity = int(quantity)
        if quantity <= 0:
            return False, "卖出数量无效"

        for p in self._portfolio.positions:
            if p.code != code:
                continue
            old_qty = p.quantity
            new_qty = old_qty - quantity
            if new_qty <= 0:
                self.remove_position(code)
                return True, f"实盘已移除 {p.name}({code})（卖出 {quantity} 股）"
            self.upsert_position(
                code=p.code,
                name=p.name,
                quantity=new_qty,
                cost_price=p.cost_price,
                buy_date=p.buy_date,
                strategy=p.strategy,
                note=p.note,
            )
            return True, f"实盘 {p.name} 数量 {old_qty} → {new_qty}（卖出 {quantity} 股）"

        return False, f"实盘无 {code}，仅记录交易日记"

    def apply_buy(self, code: str, name: str, quantity: int, cost_price: float, **kwargs) -> tuple[bool, str]:
        """交易买入后增加或合并实盘持仓（加权成本）。"""
        code = str(code).zfill(6)
        quantity = int(quantity)
        cost_price = float(cost_price)
        if quantity <= 0 or cost_price <= 0:
            return False, "买入数量或价格无效"

        for p in self._portfolio.positions:
            if p.code == code:
                old_qty = p.quantity
                old_cost = p.cost_price
                total_cost = old_cost * old_qty + cost_price * quantity
                new_qty = old_qty + quantity
                avg_cost = round(total_cost / new_qty, 4)
                self.upsert_position(
                    code=code,
                    name=name or p.name,
                    quantity=new_qty,
                    cost_price=avg_cost,
                    buy_date=kwargs.get("buy_date", p.buy_date),
                    strategy=kwargs.get("strategy", p.strategy),
                    note=kwargs.get("note", p.note),
                )
                return True, f"实盘 {p.name} 数量 {old_qty} → {new_qty}，成本 {old_cost} → {avg_cost}"

        self.upsert_position(
            code=code,
            name=name,
            quantity=quantity,
            cost_price=cost_price,
            buy_date=kwargs.get("buy_date", ""),
            strategy=kwargs.get("strategy", "手动"),
            note=kwargs.get("note", ""),
        )
        return True, f"实盘已新增 {name}({code}) {quantity} 股 @{cost_price}"

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
            "config_path": str(self.config_path),
        }

    def generate_suggestions(self, spot_df: Optional[pd.DataFrame] = None) -> List[str]:
        stats = self.analyze(spot_df)
        suggestions: List[str] = []

        if not stats.get("has_data"):
            suggestions.append(
                "尚未录入持仓。编辑 data/portfolio_config.json 后运行 "
                "`python daily_advisor.py portfolio --init`。"
            )
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

    def generate_action_suggestions(
        self,
        ultra_short: Optional[List[dict]] = None,
        spot_df: Optional[pd.DataFrame] = None,
        midterm_advice: Optional[dict] = None,
    ) -> List[str]:
        """实盘中线为主：个股复盘 + 持仓优化；超短逻辑仅用于超短策略持仓。"""
        stats = self.analyze(spot_df)
        if not stats.get("has_data"):
            return ["暂无实盘持仓。请编辑 data/portfolio_config.json 并同步。"]

        if midterm_advice is None:
            midterm_advice = MidtermPortfolioAdvisor().run_quick_advice(stats)

        actions: List[str] = []
        actions.extend(midterm_advice.get("review_summaries", [])[:6])
        actions.extend(midterm_advice.get("optimize_suggestions", []))

        ultra_map = {str(u.get("code", "")).zfill(6): u for u in (ultra_short or [])}
        positions = stats["positions"]
        scanner = UltraShortScanner()
        codes = [str(p["code"]).zfill(6) for p in positions]
        quote_map = get_realtime_quotes(codes) if codes else pd.DataFrame()
        qindex = quote_map.set_index("code") if not quote_map.empty else pd.DataFrame()

        for p in positions:
            code = str(p["code"]).zfill(6)
            strategy = str(p.get("strategy", "手动"))
            if strategy not in ("超短", "涨停"):
                continue
            name = p["name"]
            weight = p["weight_pct"]
            if code not in qindex.index:
                continue
            strength = scanner.check_live_strength(code, qindex.loc[code])
            if strength.get("hold_no_sell"):
                actions.append(
                    f"【超短仓·{name}】强势封板，超短策略可继续持有；"
                    f"若按中线管理请切换策略标签。"
                )
            elif code in ultra_map:
                u = ultra_map[code]
                actions.append(
                    f"【超短仓·{name}】超短榜评分 {u.get('ultra_short_score', 0)}，"
                    f"标签：{u.get('tags', '')}。"
                )
            if weight > 35:
                actions.append(f"【超短仓·{name}】单票占比 {weight:.1f}% 偏高。")

        for r in midterm_advice.get("recommendations", [])[:5]:
            actions.append(
                f"【中线推荐】{r['name']}({r['code']}) 评分{r['midterm_score']} "
                f"· {r.get('reason', '')}"
            )

        total_pnl = stats["total_float_pnl_pct"]
        invested = stats["invested_pct"]
        if total_pnl <= -2:
            actions.insert(
                0,
                f"组合浮亏 {total_pnl:.2f}%，仓位 {invested:.0f}%。"
                f"中线建议：先处理趋势破位标的，保留现金等待更好买点。",
            )
        elif total_pnl >= 5:
            actions.insert(
                0,
                f"组合浮盈 {total_pnl:.2f}%。中线建议：强势仓持有，弱势仓逢高换仓。",
            )

        return actions

    def print_summary(self, spot_df: Optional[pd.DataFrame] = None) -> Dict:
        stats = self.analyze(spot_df)
        print("=" * 60)
        print("个人仓位")
        print("=" * 60)
        print(f"配置文件:   {self.config_path}")
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


def init_from_config(config_path: Optional[Path] = None) -> PortfolioManager:
    """从 portfolio_config.json 初始化并写入 portfolio.json。"""
    pm = PortfolioManager()
    pm.apply_config(config_path)
    return pm


# 兼容旧调用
init_default_portfolio = init_from_config

__all__ = [
    "Position",
    "Portfolio",
    "PortfolioManager",
    "PORTFOLIO_CONFIG_FILE",
    "PORTFOLIO_FILE",
    "load_portfolio_config",
    "init_from_config",
    "init_default_portfolio",
]


if __name__ == "__main__":
    pm = PortfolioManager()
    if not pm.list_positions():
        pm = init_from_config()
        print(f"已从配置写入: {PORTFOLIO_FILE}")
    pm.print_summary()
