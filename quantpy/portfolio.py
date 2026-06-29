"""
个人仓位管理
记录总资金、当前持仓，并结合行情计算市值、浮盈亏、仓位占比。

持仓与总资金配置见 data/portfolio_config.json（编辑后运行 portfolio --init 同步）。
运行时数据保存在 data/portfolio.json（git 忽略）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd

from quantpy.paths import DATA_DIR, PORTFOLIO_CONFIG_FILE, PORTFOLIO_FILE
from quantpy.stock_data import get_latest_price, get_realtime_quotes
from quantpy.ultra_short_scanner import UltraShortScanner

ULTRA_SHORT_STRATEGIES = frozenset({"超短", "涨停"})
DEFAULT_ULTRA_SHORT_CAPITAL = 20_000.0
DEFAULT_MIDTERM_CAPITAL = 150_000.0


def classify_bucket(strategy: str) -> str:
    """超短/涨停 → ultra_short，其余 → midterm（中线）。"""
    return "ultra_short" if str(strategy) in ULTRA_SHORT_STRATEGIES else "midterm"


def bucket_label(bucket: str) -> str:
    return "超短" if bucket == "ultra_short" else "中线"


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
class ClosedPosition:
    """已清盘（全部或部分卖出）的实盘记录。"""

    code: str
    name: str
    quantity: int
    cost_price: float
    sell_price: float
    buy_date: str = ""
    sell_date: str = ""
    strategy: str = "手动"
    bucket: str = "midterm"
    profit_amount: float = 0.0
    profit_pct: float = 0.0
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    closed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Portfolio:
    ultra_short_capital: float = DEFAULT_ULTRA_SHORT_CAPITAL
    midterm_capital: float = DEFAULT_MIDTERM_CAPITAL
    total_capital: float = DEFAULT_ULTRA_SHORT_CAPITAL + DEFAULT_MIDTERM_CAPITAL
    initial_ultra_short_capital: Optional[float] = None
    initial_midterm_capital: Optional[float] = None
    positions: List[Position] = field(default_factory=list)
    closed_positions: List[ClosedPosition] = field(default_factory=list)
    journal_closed_synced: bool = False
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def sync_total_capital(self) -> None:
        self.total_capital = self.ultra_short_capital + self.midterm_capital


def load_portfolio_config(path: Path = PORTFOLIO_CONFIG_FILE) -> dict:
    """读取持仓配置文件。"""
    if not path.exists():
        return {
            "ultra_short_capital": DEFAULT_ULTRA_SHORT_CAPITAL,
            "midterm_capital": DEFAULT_MIDTERM_CAPITAL,
            "total_capital": DEFAULT_ULTRA_SHORT_CAPITAL + DEFAULT_MIDTERM_CAPITAL,
            "positions": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_positions(raw_positions: list) -> List[Position]:
    positions = []
    for p in raw_positions:
        positions.append(
            Position(**{k: v for k, v in p.items() if k in Position.__dataclass_fields__})
        )
    return positions


def _parse_closed_positions(raw_closed: list) -> List[ClosedPosition]:
    closed = []
    for item in raw_closed or []:
        payload = {k: v for k, v in item.items() if k in ClosedPosition.__dataclass_fields__}
        if "bucket" not in payload and "strategy" in payload:
            payload["bucket"] = classify_bucket(str(payload["strategy"]))
        closed.append(ClosedPosition(**payload))
    return closed


def portfolio_from_dict(raw: dict) -> Portfolio:
    ultra = float(raw.get("ultra_short_capital", DEFAULT_ULTRA_SHORT_CAPITAL))
    mid = float(raw.get("midterm_capital", DEFAULT_MIDTERM_CAPITAL))
    if "ultra_short_capital" not in raw and "midterm_capital" not in raw:
        legacy_total = float(raw.get("total_capital", ultra + mid))
        if legacy_total != ultra + mid:
            ultra = DEFAULT_ULTRA_SHORT_CAPITAL
            mid = DEFAULT_MIDTERM_CAPITAL
    portfolio = Portfolio(
        ultra_short_capital=ultra,
        midterm_capital=mid,
        total_capital=float(raw.get("total_capital", ultra + mid)),
        initial_ultra_short_capital=raw.get("initial_ultra_short_capital"),
        initial_midterm_capital=raw.get("initial_midterm_capital"),
        positions=_parse_positions(raw.get("positions", [])),
        closed_positions=_parse_closed_positions(raw.get("closed_positions", [])),
        journal_closed_synced=bool(raw.get("journal_closed_synced", False)),
        updated_at=raw.get("updated_at", datetime.now().isoformat()),
    )
    portfolio.sync_total_capital()
    if portfolio.initial_ultra_short_capital is None:
        portfolio.initial_ultra_short_capital = ultra
    if portfolio.initial_midterm_capital is None:
        portfolio.initial_midterm_capital = mid
    return portfolio


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
        self._portfolio.sync_total_capital()
        self._portfolio.updated_at = datetime.now().isoformat()
        payload = {
            "ultra_short_capital": self._portfolio.ultra_short_capital,
            "midterm_capital": self._portfolio.midterm_capital,
            "total_capital": self._portfolio.total_capital,
            "initial_ultra_short_capital": self._portfolio.initial_ultra_short_capital,
            "initial_midterm_capital": self._portfolio.initial_midterm_capital,
            "journal_closed_synced": self._portfolio.journal_closed_synced,
            "updated_at": self._portfolio.updated_at,
            "positions": [asdict(p) for p in self._portfolio.positions],
            "closed_positions": [c.to_dict() for c in self._portfolio.closed_positions],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_to_config(self) -> None:
        """同步写入 portfolio.json 与 portfolio_config.json。"""
        self.save()
        self._portfolio.sync_total_capital()
        config_payload = {
            "ultra_short_capital": int(self._portfolio.ultra_short_capital)
            if self._portfolio.ultra_short_capital == int(self._portfolio.ultra_short_capital)
            else self._portfolio.ultra_short_capital,
            "midterm_capital": int(self._portfolio.midterm_capital)
            if self._portfolio.midterm_capital == int(self._portfolio.midterm_capital)
            else self._portfolio.midterm_capital,
            "total_capital": int(self._portfolio.total_capital)
            if self._portfolio.total_capital == int(self._portfolio.total_capital)
            else self._portfolio.total_capital,
            "initial_ultra_short_capital": self._portfolio.initial_ultra_short_capital,
            "initial_midterm_capital": self._portfolio.initial_midterm_capital,
            "journal_closed_synced": self._portfolio.journal_closed_synced,
            "positions": [asdict(p) for p in self._portfolio.positions],
            "closed_positions": [c.to_dict() for c in self._portfolio.closed_positions],
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
        """兼容旧接口：按比例缩放两档资金。"""
        self._portfolio.sync_total_capital()
        old = self._portfolio.total_capital or 1.0
        ratio = float(amount) / old
        self._portfolio.ultra_short_capital = round(self._portfolio.ultra_short_capital * ratio, 2)
        self._portfolio.midterm_capital = round(self._portfolio.midterm_capital * ratio, 2)
        self._portfolio.sync_total_capital()
        self.save_to_config()

    def set_capital_buckets(
        self,
        ultra_short_capital: Optional[float] = None,
        midterm_capital: Optional[float] = None,
    ) -> None:
        if ultra_short_capital is not None:
            self._portfolio.ultra_short_capital = float(ultra_short_capital)
        if midterm_capital is not None:
            self._portfolio.midterm_capital = float(midterm_capital)
        self._portfolio.sync_total_capital()
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
        strategy: str = "中线",
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

        return False, f"实盘无 {code}，仅记录交易日记"

    def _adjust_bucket_capital(self, bucket: str, profit_amount: float) -> None:
        if bucket == "ultra_short":
            self._portfolio.ultra_short_capital = round(
                self._portfolio.ultra_short_capital + profit_amount, 2
            )
        else:
            self._portfolio.midterm_capital = round(
                self._portfolio.midterm_capital + profit_amount, 2
            )
        self._portfolio.sync_total_capital()

    def _record_closed_trade(
        self,
        *,
        code: str,
        name: str,
        quantity: int,
        cost_price: float,
        sell_price: float,
        buy_date: str = "",
        sell_date: str = "",
        strategy: str = "手动",
        note: str = "",
        adjust_capital: bool = True,
        trade_id: str = "",
    ) -> ClosedPosition:
        qty = int(quantity)
        cost = float(cost_price)
        sell = float(sell_price)
        profit_amount = round((sell - cost) * qty, 2)
        profit_pct = round((sell - cost) / cost * 100, 2) if cost > 0 else 0.0
        bucket = classify_bucket(strategy)
        closed = ClosedPosition(
            code=str(code).zfill(6),
            name=name,
            quantity=qty,
            cost_price=round(cost, 4),
            sell_price=round(sell, 4),
            buy_date=buy_date[:10] if buy_date else "",
            sell_date=sell_date[:10] if sell_date else datetime.now().strftime("%Y-%m-%d"),
            strategy=strategy,
            bucket=bucket,
            profit_amount=profit_amount,
            profit_pct=profit_pct,
            note=note,
            id=trade_id or uuid.uuid4().hex[:12],
        )
        self._portfolio.closed_positions.append(closed)
        if adjust_capital:
            self._adjust_bucket_capital(bucket, profit_amount)
        return closed

    def sync_closed_from_journal(self) -> int:
        """首次将交易日记中的已平仓记录同步到实盘清盘统计（并调整总资金）。"""
        if self._portfolio.journal_closed_synced:
            return 0
        try:
            from quantpy.trade_journal import TradeJournal

            journal = TradeJournal()
            df = journal.list_trades()
        except Exception:
            self._portfolio.journal_closed_synced = True
            self.save_to_config()
            return 0

        if df.empty:
            self._portfolio.journal_closed_synced = True
            self.save_to_config()
            return 0

        existing = {
            (c.code, c.sell_date, c.quantity, round(c.sell_price, 4))
            for c in self._portfolio.closed_positions
        }
        added = 0
        for row in df.sort_values("sell_date").to_dict("records"):
            sell_p = round(float(row.get("sell_price", 0)), 4)
            if sell_p <= 0:
                continue
            key = (
                str(row["code"]).zfill(6),
                str(row.get("sell_date", ""))[:10],
                int(row.get("quantity", 0)),
                sell_p,
            )
            if key in existing:
                continue
            self._record_closed_trade(
                code=key[0],
                name=str(row.get("name", key[0])),
                quantity=key[2],
                cost_price=float(row.get("buy_price", 0)),
                sell_price=key[3],
                buy_date=str(row.get("buy_date", "")),
                sell_date=key[1],
                strategy=str(row.get("strategy", "手动")),
                note=str(row.get("note", "")),
                adjust_capital=True,
                trade_id=str(row.get("id", "")),
            )
            existing.add(key)
            added += 1

        self._portfolio.journal_closed_synced = True
        self.save_to_config()
        return added

    def apply_sell(
        self,
        code: str,
        quantity: int,
        sell_price: Optional[float] = None,
        sell_date: str = "",
        strategy: str = "",
        note: str = "",
    ) -> tuple[bool, str]:
        """交易卖出后扣减实盘持仓；提供卖出价时记入清盘历史并调整账户资金。"""
        code = str(code).zfill(6)
        quantity = int(quantity)
        if quantity <= 0:
            return False, "卖出数量无效"

        for p in self._portfolio.positions:
            if p.code != code:
                continue
            old_qty = p.quantity
            if quantity > old_qty:
                quantity = old_qty
            sell_qty = quantity
            pos_strategy = strategy or p.strategy
            msg_extra = ""

            if sell_price is not None and float(sell_price) > 0:
                closed = self._record_closed_trade(
                    code=p.code,
                    name=p.name,
                    quantity=sell_qty,
                    cost_price=p.cost_price,
                    sell_price=float(sell_price),
                    buy_date=p.buy_date,
                    sell_date=sell_date,
                    strategy=pos_strategy,
                    note=note,
                    adjust_capital=True,
                )
                msg_extra = (
                    f"，已实现 {closed.profit_amount:+.2f} 元（{closed.profit_pct:+.2f}%），"
                    f"总资金 → {self._portfolio.total_capital:,.0f} 元"
                )

            new_qty = old_qty - sell_qty
            if new_qty <= 0:
                self._portfolio.positions = [
                    x for x in self._portfolio.positions if x.code != code
                ]
                self.save_to_config()
                return True, f"实盘已清盘 {p.name}({code})（卖出 {sell_qty} 股）{msg_extra}"

            self.upsert_position(
                code=p.code,
                name=p.name,
                quantity=new_qty,
                cost_price=p.cost_price,
                buy_date=p.buy_date,
                strategy=p.strategy,
                note=p.note,
            )
            return True, f"实盘 {p.name} 数量 {old_qty} → {new_qty}（卖出 {sell_qty} 股）{msg_extra}"

        return False, f"实盘无 {code}，仅记录交易日记"

    def update_position_cost(self, code: str, cost_price: float) -> tuple[bool, str]:
        """仅修改持仓成本价（数量、策略等保持不变）。"""
        code = str(code).zfill(6)
        cost_price = float(cost_price)
        if cost_price <= 0:
            return False, "成本价须大于 0"

        for p in self._portfolio.positions:
            if p.code != code:
                continue
            old_cost = p.cost_price
            self.upsert_position(
                code=p.code,
                name=p.name,
                quantity=p.quantity,
                cost_price=round(cost_price, 4),
                buy_date=p.buy_date,
                strategy=p.strategy,
                note=p.note,
            )
            return True, f"{p.name}({code}) 成本 {old_cost} → {round(cost_price, 4)}"

        return False, f"未找到持仓 {code}"

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
            strategy=kwargs.get("strategy", "中线"),
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
        self.sync_closed_from_journal()
        codes = [p.code for p in self._portfolio.positions]
        if spot_df is not None and not spot_df.empty:
            quote_map = spot_df
        else:
            quote_map = get_realtime_quotes(codes) if codes else pd.DataFrame()

        rows = []
        total_market_value = 0.0
        total_cost = 0.0
        from quantpy.stock_pnl_history import build_pnl_summary_by_code

        pnl_by_code = build_pnl_summary_by_code()

        for pos in self._portfolio.positions:
            price = self._fetch_price(pos.code, quote_map)
            market_value = price * pos.quantity
            cost_amount = pos.cost_amount
            profit_amount = market_value - cost_amount
            profit_pct = (price - pos.cost_price) / pos.cost_price * 100 if pos.cost_price > 0 else 0
            bucket = classify_bucket(pos.strategy)

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
                "bucket": bucket,
                "bucket_label": bucket_label(bucket),
                "strategy": pos.strategy,
                "buy_date": pos.buy_date,
                "note": pos.note,
                "historical_pnl": pnl_by_code.get(str(pos.code).zfill(6), {
                    "trade_count": 0,
                    "total_profit_amount": 0.0,
                    "win_rate": 0.0,
                }),
            })
            total_market_value += market_value
            total_cost += cost_amount

        ultra_capital = self._portfolio.ultra_short_capital
        mid_capital = self._portfolio.midterm_capital
        self._portfolio.sync_total_capital()
        total_capital = self._portfolio.total_capital

        for row in rows:
            cap = ultra_capital if row["bucket"] == "ultra_short" else mid_capital
            row["weight_pct"] = round(
                row["market_value"] / cap * 100 if cap > 0 else 0, 2
            )

        def _bucket_summary(bucket_key: str, cap: float) -> dict:
            items = [r for r in rows if r["bucket"] == bucket_key]
            cost = sum(r["cost_amount"] for r in items)
            mv = sum(r["market_value"] for r in items)
            pnl = mv - cost
            return {
                "capital": cap,
                "label": bucket_label(bucket_key),
                "cost": round(cost, 2),
                "market_value": round(mv, 2),
                "float_pnl": round(pnl, 2),
                "float_pnl_pct": round(pnl / cost * 100, 2) if cost > 0 else 0,
                "invested_pct": round(cost / cap * 100, 2) if cap > 0 else 0,
                "cash_estimated": round(max(cap - cost, 0), 2),
                "position_count": len(items),
                "positions": items,
            }

        buckets = {
            "ultra_short": _bucket_summary("ultra_short", ultra_capital),
            "midterm": _bucket_summary("midterm", mid_capital),
        }

        cash = max(total_capital - total_cost, 0)
        equity = cash + total_market_value
        total_float_pnl = total_market_value - total_cost
        invested_pct = round(total_cost / total_capital * 100, 2) if total_capital > 0 else 0
        position_count = len(rows)

        closed_rows = [c.to_dict() for c in self._portfolio.closed_positions]
        closed_rows.sort(key=lambda x: x.get("sell_date", ""), reverse=True)
        total_realized_pnl = round(sum(c.profit_amount for c in self._portfolio.closed_positions), 2)
        realized_ultra = round(
            sum(c.profit_amount for c in self._portfolio.closed_positions if c.bucket == "ultra_short"),
            2,
        )
        realized_mid = round(
            sum(c.profit_amount for c in self._portfolio.closed_positions if c.bucket == "midterm"),
            2,
        )
        closed_count = len(closed_rows)
        closed_wins = sum(1 for c in self._portfolio.closed_positions if c.profit_amount > 0)
        closed_win_rate = round(closed_wins / closed_count * 100, 1) if closed_count else 0.0
        total_pnl = round(total_realized_pnl + total_float_pnl, 2)
        initial_total = float(
            (self._portfolio.initial_ultra_short_capital or ultra_capital)
            + (self._portfolio.initial_midterm_capital or mid_capital)
        )
        total_return_pct = round(total_pnl / initial_total * 100, 2) if initial_total > 0 else 0.0

        return {
            "has_data": position_count > 0 or closed_count > 0,
            "ultra_short_capital": ultra_capital,
            "midterm_capital": mid_capital,
            "initial_ultra_short_capital": self._portfolio.initial_ultra_short_capital,
            "initial_midterm_capital": self._portfolio.initial_midterm_capital,
            "initial_total_capital": round(initial_total, 2),
            "total_capital": total_capital,
            "buckets": buckets,
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market_value, 2),
            "cash_estimated": round(cash, 2),
            "equity_estimated": round(equity, 2),
            "total_float_pnl": round(total_float_pnl, 2),
            "total_float_pnl_pct": round(total_float_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
            "total_realized_pnl": total_realized_pnl,
            "realized_pnl_ultra_short": realized_ultra,
            "realized_pnl_midterm": realized_mid,
            "total_pnl": total_pnl,
            "total_return_pct": total_return_pct,
            "closed_count": closed_count,
            "closed_win_rate": closed_win_rate,
            "closed_positions": closed_rows,
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
            f"总资金 {total_capital/10000:.1f} 万"
            f"（超短 {stats['ultra_short_capital']/10000:.1f}万 + "
            f"中线 {stats['midterm_capital']/10000:.1f}万），"
            f"已投入 {stats['total_cost']:.0f} 元（{invested_pct}%），"
            f"已实现 {stats.get('total_realized_pnl', 0):+.0f} 元，"
            f"浮盈 {stats['total_float_pnl']:+.0f} 元（{stats['total_float_pnl_pct']:+.2f}%），"
            f"累计 {stats.get('total_pnl', stats['total_float_pnl']):+.0f} 元。"
        )
        if stats.get("closed_count", 0) > 0:
            suggestions.append(
                f"已清盘 {stats['closed_count']} 笔，胜率 {stats.get('closed_win_rate', 0)}%，"
                f"已实现盈亏已计入总资金。"
            )

        for key, label in (("ultra_short", "超短"), ("midterm", "中线")):
            b = stats.get("buckets", {}).get(key, {})
            if not b.get("position_count"):
                suggestions.append(f"{label}账户（{b.get('capital', 0)/10000:.1f}万）：暂无持仓。")
                continue
            suggestions.append(
                f"{label}账户：投入 {b['cost']:.0f}/{b['capital']:.0f} 元"
                f"（{b['invested_pct']:.0f}%），"
                f"浮盈 {b['float_pnl']:+.0f} 元（{b['float_pnl_pct']:+.2f}%），"
                f"{b['position_count']} 只。"
            )
            if b["invested_pct"] > 95:
                suggestions.append(f"{label}账户接近满仓，注意保留机动资金。")

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
                f"{max_weight['bucket_label']}·{max_weight['name']} 占该账户 "
                f"{max_weight['weight_pct']:.1f}% 偏高，建议单票≤30%。"
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
            from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor

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
            if p.get("bucket") != "ultra_short":
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
        print(
            f"  超短账户: {stats['ultra_short_capital']:,.0f} 元 | "
            f"中线账户: {stats['midterm_capital']:,.0f} 元"
        )
        print(f"持仓成本:   {stats['total_cost']:,.0f} 元 ({stats['invested_pct']:.1f}%)")
        print(f"持仓市值:   {stats['total_market_value']:,.0f} 元")
        print(f"预估现金:   {stats['cash_estimated']:,.0f} 元")
        print(f"账户权益:   {stats['equity_estimated']:,.0f} 元")
        print(f"浮动盈亏:   {stats['total_float_pnl']:+,.0f} 元 ({stats['total_float_pnl_pct']:+.2f}%)")
        print(
            f"已实现盈亏: {stats.get('total_realized_pnl', 0):+,.0f} 元 | "
            f"累计盈亏: {stats.get('total_pnl', 0):+,.0f} 元 ({stats.get('total_return_pct', 0):+.2f}%)"
        )
        if stats.get("closed_count", 0) > 0:
            print(
                f"清盘记录:   {stats['closed_count']} 笔，胜率 {stats.get('closed_win_rate', 0)}%"
            )
        if stats.get("trade_date"):
            print(f"行情日期:   {stats['trade_date']}  (腾讯实时)")
        print("-" * 60)

        if stats["positions"]:
            df = pd.DataFrame(stats["positions"])
            cols = ["code", "name", "bucket_label", "strategy", "quantity", "cost_price",
                    "current_price", "profit_pct", "market_value", "weight_pct"]
            print(df[cols].to_string(index=False))
        else:
            print("（暂无持仓）")
        if stats.get("closed_positions"):
            print("-" * 60)
            print("清盘记录（最近5笔）")
            cdf = pd.DataFrame(stats["closed_positions"][:5])
            cols = ["sell_date", "code", "name", "bucket", "quantity", "cost_price",
                    "sell_price", "profit_pct", "profit_amount"]
            print(cdf[cols].to_string(index=False))
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
    "ClosedPosition",
    "Portfolio",
    "PortfolioManager",
    "PORTFOLIO_CONFIG_FILE",
    "PORTFOLIO_FILE",
    "ULTRA_SHORT_STRATEGIES",
    "DEFAULT_ULTRA_SHORT_CAPITAL",
    "DEFAULT_MIDTERM_CAPITAL",
    "classify_bucket",
    "bucket_label",
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
