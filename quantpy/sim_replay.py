"""
模拟复盘系统
- 每个交易日 9:30-9:45 超短选股（最多 3 只）
- 模拟资金 20 万，自动买卖与持仓管理
- A 股 T+1：当日买入次日方可卖出
- 每 5 个交易日自动复盘，优化选票与买卖点参数
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from quantpy.paths import DATA_DIR, SIM_REVIEW_DIR, SIM_STATE_FILE
from quantpy.sim_midterm import ensure_midterm_state
from quantpy.qstock_strategy_optimizer import StrategyOptimizer
from quantpy.stock_data import get_market_spot, get_realtime_quotes, get_stock_hist
from quantpy.ultra_short_scanner import UltraShortScanner

OUTPUT_DIR = SIM_REVIEW_DIR


@dataclass
class SimConfig:
    capital: float = 200_000.0
    max_positions: int = 3
    select_start: str = "09:30"
    select_end: str = "09:45"
    review_interval: int = 5
    stop_loss_pct: float = -3.0
    take_profit_pct: float = 8.0
    max_hold_days: int = 3
    min_score: int = 40
    max_open_gap_pct: float = 7.0
    min_open_gap_pct: float = 0.5
    buy_premium_pct: float = 0.5  # 9:45 买入相对开盘价溢价估算
    top_prefilter: int = 200
    t_plus_one: bool = True  # A股 T+1：买入当日不可卖出


@dataclass
class SimPosition:
    code: str
    name: str
    quantity: int
    buy_price: float
    buy_date: str
    stop_loss: float
    take_profit: float
    score: float = 0.0
    tags: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


@dataclass
class SimTrade:
    code: str
    name: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    quantity: int
    profit_pct: float
    profit_amount: float
    hold_days: int
    exit_reason: str
    score: float = 0.0


class MorningSelector:
    """9:30-9:45 早盘超短选股（强化版）。"""

    def __init__(self, config: SimConfig):
        self.config = config
        self.scanner = UltraShortScanner()
        self.optimizer = StrategyOptimizer()

    def _morning_filters(self, item: dict, quote: Optional[pd.Series]) -> Tuple[bool, str, float]:
        """返回 (是否通过, 拒绝原因, 早盘评分加成)。"""
        if quote is None or (isinstance(quote, pd.Series) and quote.empty):
            return True, "", 0.0

        open_px = float(quote.get("open", item.get("price", 0)))
        pre_close = float(quote.get("pre_close", 0))
        current = float(quote.get("close", open_px))

        if pre_close <= 0 or open_px <= 0:
            return True, "", 0.0

        gap_pct = (open_px - pre_close) / pre_close * 100
        intraday_pct = (current - open_px) / open_px * 100 if open_px > 0 else 0

        if gap_pct > self.config.max_open_gap_pct:
            return False, f"高开过大{gap_pct:.1f}%", 0.0
        if gap_pct < self.config.min_open_gap_pct:
            return False, f"低开/平开{gap_pct:.1f}%", 0.0

        bonus = 0.0
        tags = []
        if 1.0 <= gap_pct <= 4.0:
            bonus += 8
            tags.append("理想高开")
        if -1.0 <= intraday_pct <= 2.0:
            bonus += 6
            tags.append("9:45未急拉")
        if intraday_pct > 5.0:
            return False, "开盘15分钟急拉", 0.0

        buy_price = round(open_px * (1 + self.config.buy_premium_pct / 100), 2)
        item["open_price"] = round(open_px, 2)
        item["pre_close"] = round(pre_close, 2)
        item["gap_pct"] = round(gap_pct, 2)
        item["buy_price_suggest"] = buy_price
        item["buy_zone"] = f"{round(open_px * 0.998, 2)}-{round(open_px * 1.02, 2)}"
        item["morning_bonus"] = bonus
        if tags:
            item["tags"] = item.get("tags", "") + "," + ",".join(tags)
        return True, "", bonus

    def select(
        self,
        trade_date: Optional[str] = None,
        show_progress: bool = True,
    ) -> pd.DataFrame:
        if show_progress:
            print(f"早盘选股窗口 {self.config.select_start}-{self.config.select_end}")

        market = get_market_spot(verbose=show_progress, force_refresh=False)
        if market.empty:
            return pd.DataFrame()

        picks = self.scanner.scan_market(
            stock_list=market,
            top_prefilter=self.config.top_prefilter,
            min_score=self.config.min_score,
            max_workers=8,
            show_progress=show_progress,
        )
        if picks.empty:
            return pd.DataFrame()

        codes = picks["code"].astype(str).tolist()
        quotes = get_realtime_quotes(codes)
        quote_map = quotes.set_index("code") if not quotes.empty else pd.DataFrame()

        filtered = []
        for _, row in picks.iterrows():
            code = str(row["code"]).zfill(6)
            item = row.to_dict()
            q = quote_map.loc[code] if code in quote_map.index else None
            ok, reason, bonus = self._morning_filters(item, q)
            if ok:
                item["final_score"] = item["ultra_short_score"] + bonus
                filtered.append(item)
            elif show_progress and len(filtered) < 3:
                pass  # 静默过滤

        if not filtered:
            return pd.DataFrame()

        df = pd.DataFrame(filtered).sort_values("final_score", ascending=False)
        df = df.head(self.config.max_positions).reset_index(drop=True)
        df["rank"] = range(1, len(df) + 1)
        return df


class SimReplayEngine:
    """模拟交易引擎 + 定期复盘。"""

    def __init__(self, config: Optional[SimConfig] = None):
        self.config = config or SimConfig()
        self.selector = MorningSelector(self.config)
        self.scanner = UltraShortScanner()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if SIM_STATE_FILE.exists():
            try:
                state = json.loads(SIM_STATE_FILE.read_text(encoding="utf-8"))
                ensure_midterm_state(state)
                return state
            except json.JSONDecodeError:
                pass
        return self._default_state()

    def _default_state(self) -> dict:
        state = {
            "config": asdict(self.config),
            "cash": self.config.capital,
            "initial_capital": self.config.capital,
            "positions": [],
            "closed_trades": [],
            "trading_day_count": 0,
            "last_select_date": "",
            "last_review_date": "",
            "review_round": 0,
            "param_history": [],
            "updated_at": datetime.now().isoformat(),
        }
        ensure_midterm_state(state)
        return state

    def _save_state(self) -> None:
        self.state["updated_at"] = datetime.now().isoformat()
        self.state["config"] = asdict(self.config)
        SIM_STATE_FILE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def reload_state(self) -> None:
        """从磁盘重新加载状态（避免进程内缓存与文件不一致）。"""
        self.state = self._load_state()

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _norm_date(self, value: Optional[str]) -> str:
        return str(value or "")[:10]

    def _is_sellable(self, buy_date: str, as_of_date: Optional[str] = None) -> bool:
        """A股 T+1：买入当日不可卖，次日及以后可卖。"""
        if not self.config.t_plus_one:
            return True
        return self._norm_date(buy_date) < self._norm_date(as_of_date or self._today())

    def _hold_calendar_days(self, buy_date: str, as_of_date: Optional[str] = None) -> int:
        buy = pd.Timestamp(self._norm_date(buy_date))
        as_of = pd.Timestamp(self._norm_date(as_of_date or self._today()))
        return max(int((as_of - buy).days), 0)

    def _is_select_window(self) -> bool:
        now = datetime.now().time()
        start = time(*map(int, self.config.select_start.split(":")))
        end = time(*map(int, self.config.select_end.split(":")))
        return start <= now <= end

    def _calc_quantity(self, buy_price: float, slots: int) -> int:
        if buy_price <= 0 or slots <= 0:
            return 0
        cash = float(self.state["cash"])
        budget = cash / slots
        qty = int(budget / buy_price / 100) * 100
        return max(qty, 0)

    def run_morning_select(self, force: bool = False, show_progress: bool = True) -> pd.DataFrame:
        today = self._today()
        if self.state["last_select_date"] == today and not force:
            if show_progress:
                print(f"今日 {today} 已完成选股，使用 --force 强制重选")
            return pd.DataFrame()

        if not force and not self._is_select_window():
            if show_progress:
                print(f"当前非选股窗口 ({self.config.select_start}-{self.config.select_end})，使用 --force 强制执行")

        open_slots = self.config.max_positions - len(self.state["positions"])
        if open_slots <= 0:
            if show_progress:
                print("持仓已满 3 只，今日不再新开仓")
            self.state["last_select_date"] = today
            self._save_state()
            return pd.DataFrame()

        picks = self.selector.select(trade_date=today, show_progress=show_progress)
        if picks.empty:
            if show_progress:
                print("早盘无符合条件标的")
            self.state["last_select_date"] = today
            self._save_state()
            return picks

        picks = picks.head(open_slots)
        bought = []

        for _, row in picks.iterrows():
            buy_price = float(row.get("buy_price_suggest", row.get("price", 0)))
            if buy_price <= 0:
                continue
            slots_left = self.config.max_positions - len(self.state["positions"])
            qty = self._calc_quantity(buy_price, slots_left)
            if qty <= 0:
                continue
            cost = buy_price * qty
            if cost > float(self.state["cash"]):
                continue

            pos = SimPosition(
                code=str(row["code"]).zfill(6),
                name=str(row["name"]),
                quantity=qty,
                buy_price=buy_price,
                buy_date=today,
                stop_loss=round(buy_price * (1 + self.config.stop_loss_pct / 100), 2),
                take_profit=round(buy_price * (1 + self.config.take_profit_pct / 100), 2),
                score=float(row.get("final_score", row.get("ultra_short_score", 0))),
                tags=str(row.get("tags", "")),
            )
            self.state["cash"] = float(self.state["cash"]) - cost
            self.state["positions"].append(asdict(pos))
            bought.append(row)

        self.state["last_select_date"] = today
        self.state["trading_day_count"] = int(self.state.get("trading_day_count", 0)) + 1
        self._save_state()

        if show_progress and bought:
            print(f"\n模拟买入 {len(bought)} 只:")
            for row in bought:
                print(
                    f"  {row['name']}({row['code']}) 建议价 {row.get('buy_price_suggest')} "
                    f"区间 {row.get('buy_zone')} 评分 {row.get('final_score', 0):.0f}"
                )
        return picks

    def check_exits_live(self, show_progress: bool = True) -> List[SimTrade]:
        """根据最新价检查止盈止损。"""
        if not self.state["positions"]:
            return []

        codes = [p["code"] for p in self.state["positions"]]
        quotes = get_realtime_quotes(codes)
        if quotes.empty:
            return []

        quote_map = quotes.set_index("code")
        today = self._today()
        closed: List[SimTrade] = []
        remain = []

        for p in self.state["positions"]:
            code = p["code"]
            if code not in quote_map.index:
                remain.append(p)
                continue

            q = quote_map.loc[code]
            price = float(q["close"])
            low = float(q.get("low", price))
            high = float(q.get("high", price))
            buy_date = self._norm_date(p["buy_date"])
            hold_days = self._hold_calendar_days(buy_date, today)

            if not self._is_sellable(buy_date, today):
                remain.append(p)
                if show_progress:
                    print(
                        f"  持有 {p['name']}({code}) T+1锁仓"
                        f"（{buy_date} 买入，次日可卖）"
                    )
                continue

            strength = self.scanner.check_live_strength(code, q)
            if strength.get("hold_no_sell"):
                remain.append(p)
                if show_progress:
                    print(
                        f"  持有 {p['name']}({code}) 当日强势封板"
                        f"（+{strength.get('pct_chg', 0):.1f}%），暂不卖"
                    )
                continue

            sell_price = None
            reason = ""

            if low <= p["stop_loss"]:
                sell_price = p["stop_loss"]
                reason = f"止损({self.config.stop_loss_pct}%)"
            elif high >= p["take_profit"]:
                sell_price = p["take_profit"]
                reason = f"止盈({self.config.take_profit_pct}%)"
            elif hold_days >= self.config.max_hold_days:
                sell_price = price
                reason = f"超短到期({self.config.max_hold_days}日)"
            elif hold_days >= 2 and price >= p["buy_price"] * 1.03:
                sell_price = price
                reason = "持2日盈利3%落袋"

            if sell_price is not None:
                trade = self._close_position(p, today, sell_price, reason, hold_days)
                closed.append(trade)
                if show_progress:
                    print(f"  卖出 {p['name']}({code}) @{sell_price} {reason} 收益 {trade.profit_pct:+.2f}%")
            else:
                remain.append(p)

        self.state["positions"] = remain
        if closed:
            self._save_state()
        return closed

    def _close_position(
        self, p: dict, sell_date: str, sell_price: float, reason: str, hold_days: int
    ) -> SimTrade:
        profit_pct = (sell_price - p["buy_price"]) / p["buy_price"] * 100
        profit_amount = (sell_price - p["buy_price"]) * p["quantity"]
        self.state["cash"] = float(self.state["cash"]) + sell_price * p["quantity"]
        trade = SimTrade(
            code=p["code"],
            name=p["name"],
            buy_date=p["buy_date"],
            buy_price=p["buy_price"],
            sell_date=sell_date,
            sell_price=round(sell_price, 2),
            quantity=p["quantity"],
            profit_pct=round(profit_pct, 2),
            profit_amount=round(profit_amount, 2),
            hold_days=hold_days,
            exit_reason=reason,
            score=p.get("score", 0),
        )
        self.state["closed_trades"].append(asdict(trade))
        return trade

    def run_daily(self, force_select: bool = False, show_progress: bool = True) -> dict:
        """每日流程：可卖持仓检查卖出 → 早盘选股买入 → 判断是否复盘。"""
        if show_progress:
            print("=" * 60)
            print("模拟复盘 - 每日运行")
            print("=" * 60)

        closed = self.check_exits_live(show_progress=show_progress)
        picks = self.run_morning_select(force=force_select, show_progress=show_progress)
        review = None
        if self._should_review():
            review = self.run_review(show_progress=show_progress)

        summary = self.get_summary()
        summary["closed_today"] = len(closed)
        summary["picks_today"] = len(picks) if picks is not None and not picks.empty else 0
        summary["review"] = review
        return summary

    def _should_review(self) -> bool:
        count = int(self.state.get("trading_day_count", 0))
        interval = self.config.review_interval
        if count > 0 and count % interval == 0:
            last = self.state.get("last_review_date", "")
            if last != self._today():
                return True
        return False

    def run_review(self, show_progress: bool = True) -> dict:
        """每 5 个交易日复盘，输出优化建议并微调参数。"""
        trades = self.state.get("closed_trades", [])
        recent = trades[-20:] if trades else []
        today = self._today()

        if show_progress:
            print("\n" + "=" * 60)
            print(f"第 {int(self.state.get('review_round', 0)) + 1} 轮复盘（每 {self.config.review_interval} 交易日）")
            print("=" * 60)

        if not recent:
            suggestions = ["样本不足，继续模拟积累至少 5 笔 closed_trades 后再复盘。"]
            stats = {"trade_count": 0, "win_rate": 0, "avg_profit": 0}
        else:
            df = pd.DataFrame(recent)
            wins = df[df["profit_pct"] > 0]
            stats = {
                "trade_count": len(df),
                "win_rate": round(len(wins) / len(df) * 100, 1),
                "avg_profit": round(df["profit_pct"].mean(), 2),
                "avg_hold": round(df["hold_days"].mean(), 1),
                "total_pnl": round(df["profit_amount"].sum(), 2),
            }
            suggestions = self._generate_review_suggestions(df, stats)

        param_changes: dict = {}
        ai_learning = None
        if recent:
            try:
                from ai_learning_optimizer import AILearningOptimizer

                ai_opt = AILearningOptimizer(auto_apply=True)
                ai_learning = ai_opt.run_learning_cycle(
                    self,
                    review_round=int(self.state.get("review_round", 0)) + 1,
                    show_progress=show_progress,
                )
                param_changes = ai_learning.get("param_changes", {})
                ai_suggestions = ai_learning.get("suggestions", [])
                seen = set(suggestions)
                for s in ai_suggestions:
                    if s not in seen:
                        suggestions.append(s)
                        seen.add(s)
            except Exception as exc:
                if show_progress:
                    print(f"  AI 学习回退规则引擎: {exc}")
                param_changes = self._auto_tune_params(stats)

        review = {
            "date": today,
            "round": int(self.state.get("review_round", 0)) + 1,
            "stats": stats,
            "suggestions": suggestions,
            "param_changes": param_changes,
            "config_after": asdict(self.config),
            "ai_learning": ai_learning,
        }

        self.state["review_round"] = review["round"]
        self.state["last_review_date"] = today
        self.state.setdefault("param_history", []).append(review)
        self._save_state()

        self._save_review_report(review)

        if show_progress:
            if stats.get("trade_count"):
                print(f"近{stats['trade_count']}笔: 胜率 {stats['win_rate']}% 均收益 {stats['avg_profit']}%")
            print("\n【复盘建议】")
            for i, s in enumerate(suggestions, 1):
                print(f"  {i}. {s}")
            if param_changes:
                print("\n【参数自动微调】")
                for k, v in param_changes.items():
                    print(f"  {k}: {v}")

        return review

    def _generate_review_suggestions(self, df: pd.DataFrame, stats: dict) -> List[str]:
        suggestions = []
        win_rate = stats["win_rate"]
        avg_profit = stats["avg_profit"]

        if win_rate < 45:
            suggestions.append(
                f"胜率 {win_rate}% 偏低：提高 min_score 门槛，减少 9:45 急拉追高（已自动收紧）。"
            )
        if avg_profit < 0:
            suggestions.append(
                f"均收益 {avg_profit}% 为负：缩短 max_hold_days，严格执行 -3% 止损。"
            )
        if stats.get("avg_hold", 0) > 2.5:
            suggestions.append("平均持仓偏长，超短策略建议 1-2 日内了结。")

        stop_exits = df[df["exit_reason"].str.contains("止损", na=False)]
        if len(stop_exits) > len(df) * 0.4:
            suggestions.append("止损触发过多：选股时优先「理想高开+未急拉」，避开高开>5%标的。")

        profit_exits = df[df["exit_reason"].str.contains("止盈", na=False)]
        if len(profit_exits) < len(df) * 0.2 and win_rate > 50:
            suggestions.append("止盈触发偏少：可略降 take_profit 或采用分批止盈。")

        by_reason = df.groupby("exit_reason")["profit_pct"].mean()
        best_reason = by_reason.idxmax() if not by_reason.empty else ""
        if best_reason:
            suggestions.append(f"最优退出方式: {best_reason}（均收益 {by_reason.max():.2f}%）")

        suggestions.append(
            "买卖点优化：买入控制在开盘价 +0.5% 附近；卖出优先止损→止盈→到期顺序判定。"
        )
        if self.config.t_plus_one:
            suggestions.append(
                "A股 T+1：当日买入无法当日卖出，止损/止盈自次一交易日生效。"
            )
        return suggestions

    def _auto_tune_params(self, stats: dict) -> dict:
        changes = {}
        if not stats.get("trade_count"):
            return changes

        if stats["win_rate"] < 45:
            old = self.config.min_score
            self.config.min_score = min(old + 5, 60)
            changes["min_score"] = f"{old} → {self.config.min_score}"

        if stats["avg_profit"] < -1:
            old = self.config.max_open_gap_pct
            self.config.max_open_gap_pct = max(old - 1.0, 4.0)
            changes["max_open_gap_pct"] = f"{old} → {self.config.max_open_gap_pct}"

        if stats.get("avg_hold", 0) > 2.5:
            old = self.config.max_hold_days
            self.config.max_hold_days = max(old - 1, 2)
            changes["max_hold_days"] = f"{old} → {self.config.max_hold_days}"

        if stats["win_rate"] >= 55 and stats["avg_profit"] > 2:
            old = self.config.take_profit_pct
            self.config.take_profit_pct = min(old + 1.0, 12.0)
            changes["take_profit_pct"] = f"{old} → {self.config.take_profit_pct}"

        return changes

    def _save_review_report(self, review: dict) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        fname = OUTPUT_DIR / f"review_{review['date']}_r{review['round']}.md"
        lines = [
            f"# 模拟复盘报告 第{review['round']}轮\n",
            f"日期: {review['date']}\n\n",
            "## 绩效\n",
            f"- 样本: {review['stats'].get('trade_count', 0)} 笔\n",
            f"- 胜率: {review['stats'].get('win_rate', 0)}%\n",
            f"- 均收益: {review['stats'].get('avg_profit', 0)}%\n\n",
            "## 建议\n",
        ]
        for i, s in enumerate(review["suggestions"], 1):
            lines.append(f"{i}. {s}\n")
        if review.get("param_changes"):
            lines.append("\n## 参数调整\n")
            for k, v in review["param_changes"].items():
                lines.append(f"- {k}: {v}\n")
        ai = review.get("ai_learning")
        if ai and ai.get("suggestions"):
            lines.append("\n## AI 策略学习\n")
            lines.append(f"引擎: {ai.get('engine', 'statistical')} | 样本: {ai.get('sample_count', 0)} 笔\n\n")
            for i, s in enumerate(ai["suggestions"][:8], 1):
                lines.append(f"{i}. {s}\n")
        fname.write_text("".join(lines), encoding="utf-8")
        return fname

    def get_summary(self) -> dict:
        positions = self.state.get("positions", [])
        cash = float(self.state.get("cash", 0))
        mv = 0.0
        if positions:
            quotes = get_realtime_quotes([p["code"] for p in positions])
            qmap = quotes.set_index("code") if not quotes.empty else pd.DataFrame()
            for p in positions:
                px = float(qmap.loc[p["code"], "close"]) if p["code"] in qmap.index else p["buy_price"]
                mv += px * p["quantity"]

        equity = cash + mv
        initial = float(self.state.get("initial_capital", self.config.capital))
        return {
            "cash": round(cash, 2),
            "market_value": round(mv, 2),
            "equity": round(equity, 2),
            "total_return_pct": round((equity - initial) / initial * 100, 2),
            "positions": positions,
            "closed_count": len(self.state.get("closed_trades", [])),
            "trading_day_count": self.state.get("trading_day_count", 0),
        }

    def print_status(self) -> dict:
        s = self.get_summary()
        print("=" * 60)
        print("模拟账户状态（20万 · 最多3仓）")
        print("=" * 60)
        print(f"现金:       {s['cash']:,.2f}")
        print(f"持仓市值:   {s['market_value']:,.2f}")
        print(f"总权益:     {s['equity']:,.2f}  ({s['total_return_pct']:+.2f}%)")
        print(f"已平仓:     {s['closed_count']} 笔 | 交易日计数: {s['trading_day_count']}")
        t1 = "开启" if self.config.t_plus_one else "关闭"
        print(f"参数: T+1={t1} 止损{self.config.stop_loss_pct}% 止盈{self.config.take_profit_pct}% "
              f"最长{self.config.max_hold_days}日 min_score={self.config.min_score}")
        print("-" * 60)
        today = self._today()
        if s["positions"]:
            for p in s["positions"]:
                sellable = self._is_sellable(p["buy_date"], today)
                lock = " T+1锁仓" if not sellable else ""
                print(
                    f"  {p['name']}({p['code']}) {p['quantity']}股 @{p['buy_price']} "
                    f"止损{p['stop_loss']} 止盈{p['take_profit']} {p['buy_date']}{lock}"
                )
        else:
            print("  （空仓）")
        print("=" * 60)
        return s

    def replay_backtest(self, days: int = 25, show_progress: bool = True) -> dict:
        """
        历史模拟复盘：逐交易日重放 9:45 选股 + 买卖规则。
        用于验证与参数优化（重置模拟账户）。
        """
        if show_progress:
            print("=" * 60)
            print(f"历史模拟复盘（近 {days} 个交易日）")
            print("=" * 60)

        ref = get_stock_hist("000001", days=days + 40, patch_live=False)
        if ref.empty or len(ref) < days:
            print("无法获取交易日历")
            return {}

        ref["date"] = pd.to_datetime(ref["date"])
        trading_days = ref["date"].dt.strftime("%Y-%m-%d").tolist()[-days:]

        self.state = self._default_state()
        self.config = SimConfig()
        self.selector = MorningSelector(self.config)
        self.scanner = UltraShortScanner()

        for day_idx, day in enumerate(trading_days):
            # 先卖（仅 T+1 可卖持仓）再买，贴合 A 股日内顺序
            self._backtest_exits(day)

            if len(self.state["positions"]) < self.config.max_positions:
                self._backtest_morning_buy(day, day_idx)

            self.state["trading_day_count"] = day_idx + 1
            if (day_idx + 1) % self.config.review_interval == 0:
                self.run_review(show_progress=False)

        self._save_state()
        summary = self.get_summary()
        if show_progress:
            print(f"\n回测完成: 权益 {summary['equity']:,.0f} ({summary['total_return_pct']:+.2f}%)")
            print(f"平仓 {summary['closed_count']} 笔")
        return summary

    def _backtest_exits(self, day: str) -> None:
        remain = []
        for p in list(self.state["positions"]):
            hist = get_stock_hist(p["code"], days=60, patch_live=False)
            if hist.empty:
                remain.append(p)
                continue
            hist["date"] = pd.to_datetime(hist["date"]).dt.strftime("%Y-%m-%d")
            bar = hist[hist["date"] == day]
            if bar.empty:
                remain.append(p)
                continue
            bar = bar.iloc[0]
            low = float(bar["low"])
            high = float(bar["high"])
            close = float(bar["close"])
            bar_pct = float(bar.get("pct_chg", 0) or 0)
            pre_close = float(bar.get("pre_close", 0) or 0)
            if pre_close <= 0:
                prev = hist[hist["date"] < day]
                if not prev.empty:
                    pre_close = float(prev.iloc[-1]["close"])
            buy_date = self._norm_date(p["buy_date"])
            hold_days = self._hold_calendar_days(buy_date, day)

            if not self._is_sellable(buy_date, day):
                remain.append(p)
                continue

            vol_ratio = self.scanner._volume_ratio(hist)
            strength = self.scanner.check_bar_strength(
                p["code"], close, high, low, bar_pct, pre_close, vol_ratio
            )
            if strength.get("hold_no_sell"):
                remain.append(p)
                continue

            sell_price = None
            reason = ""
            if low <= p["stop_loss"]:
                sell_price = p["stop_loss"]
                reason = "止损"
            elif high >= p["take_profit"]:
                sell_price = p["take_profit"]
                reason = "止盈"
            elif hold_days >= self.config.max_hold_days:
                sell_price = close
                reason = "到期"
            elif hold_days >= 2 and close >= p["buy_price"] * 1.03:
                sell_price = close
                reason = "持2日盈利"

            if sell_price is not None:
                self._close_position(p, day, sell_price, reason, hold_days)
            else:
                remain.append(p)
        self.state["positions"] = remain

    def _backtest_morning_buy(self, day: str, day_idx: int) -> None:
        """回测：前一日强势 + 当日开盘价买入。"""
        if day_idx == 0:
            return

        candidates = self._backtest_select_for_day(day)
        if not candidates:
            return

        slots = self.config.max_positions - len(self.state["positions"])
        for item in candidates[:slots]:
            buy_price = item["open_price"]
            qty = self._calc_quantity(buy_price, slots)
            if qty <= 0 or buy_price * qty > float(self.state["cash"]):
                continue
            pos = SimPosition(
                code=item["code"],
                name=item["name"],
                quantity=qty,
                buy_price=round(buy_price, 2),
                buy_date=day,
                stop_loss=round(buy_price * (1 + self.config.stop_loss_pct / 100), 2),
                take_profit=round(buy_price * (1 + self.config.take_profit_pct / 100), 2),
                score=item.get("score", 0),
            )
            self.state["cash"] -= buy_price * qty
            self.state["positions"].append(asdict(pos))
            slots -= 1

    def _backtest_select_for_day(self, day: str, sample_size: int = 150) -> List[dict]:
        """基于历史 K 线的前一日强势 + 当日高开过滤。"""
        market = get_market_spot(verbose=False)
        if market.empty:
            return []

        codes = market["code"].astype(str).str.zfill(6).head(sample_size).tolist()
        day_ts = pd.Timestamp(day)
        results = []

        for code in codes:
            hist = get_stock_hist(code, days=40, patch_live=False)
            if hist.empty or len(hist) < 6:
                continue
            hist = hist.copy()
            hist["date"] = pd.to_datetime(hist["date"])
            today_rows = hist[hist["date"].dt.strftime("%Y-%m-%d") == day]
            if today_rows.empty:
                continue
            prev_rows = hist[hist["date"] < day_ts]
            if len(prev_rows) < 2:
                continue

            prev = prev_rows.iloc[-1]
            today = today_rows.iloc[0]
            prev_pct = float(prev.get("pct_chg", 0) or 0)
            if pd.isna(prev_pct):
                prev_pct = (prev["close"] - prev_rows.iloc[-2]["close"]) / prev_rows.iloc[-2]["close"] * 100

            if prev_pct < 5.0:
                continue

            pre_close = float(prev["close"])
            open_px = float(today["open"])
            if pre_close <= 0 or open_px <= 0:
                continue
            gap_pct = (open_px - pre_close) / pre_close * 100
            if gap_pct > self.config.max_open_gap_pct or gap_pct < self.config.min_open_gap_pct:
                continue

            score = prev_pct + gap_pct * 0.5
            if prev_pct >= 9.5:
                score += 10
            name = code
            row = market[market["code"].astype(str).str.zfill(6) == code]
            if not row.empty and "name" in row.columns:
                name = str(row.iloc[0]["name"])

            results.append({
                "code": code,
                "name": name,
                "open_price": open_px,
                "gap_pct": round(gap_pct, 2),
                "prev_pct": round(prev_pct, 2),
                "score": round(score, 2),
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[: self.config.max_positions * 2]


def run_sim_daily(force: bool = False) -> dict:
    engine = SimReplayEngine()
    engine.print_status()
    return engine.run_daily(force_select=force, show_progress=True)


def run_sim_backtest(days: int = 25) -> dict:
    engine = SimReplayEngine()
    return engine.replay_backtest(days=days)


def run_sim_status() -> dict:
    engine = SimReplayEngine()
    return engine.print_status()


def run_sim_review() -> dict:
    engine = SimReplayEngine()
    return engine.run_review(show_progress=True)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if cmd == "backtest":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 25
        run_sim_backtest(days)
    elif cmd == "review":
        run_sim_review()
    elif cmd == "status":
        run_sim_status()
    else:
        force = "--force" in sys.argv
        run_sim_daily(force=force)
