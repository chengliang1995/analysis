#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日顾问：超短个股捕捉 + 个人操作学习 + 优化建议

用法:
  python daily_advisor.py              # 生成今日完整报告
  python daily_advisor.py scan         # 仅超短扫描
  python daily_advisor.py learn        # 仅学习建议（基于交易日记）
  python daily_advisor.py record       # 录入一笔交易
  python daily_advisor.py sim            # 模拟复盘（9:30-9:45选股）
  python daily_advisor.py sim-backtest   # 历史模拟回测
  python daily_advisor.py sim-review     # 手动触发复盘
  python daily_advisor.py sim-status       # 模拟账户状态
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from quantpy.paths import OUTPUT_DIR, REPORT_DIR
from quantpy.trade_journal import TradeJournal, interactive_record
from quantpy.portfolio import PortfolioManager
from quantpy.stock_data import collect_daily_market_close, get_market_spot
from quantpy.sim_replay import (
    run_sim_backtest,
    run_sim_daily,
    run_sim_review,
    run_sim_status,
)
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.ai_learning_optimizer import load_latest_ai_learning, run_ai_learning
from quantpy.midterm_portfolio_advisor import run_midterm_advice, load_latest_midterm_advice
from quantpy.midterm_level_alerts import scan_midterm_level_alerts
from quantpy.real_portfolio_reviewer import load_latest_real_review, run_real_portfolio_review
from quantpy.ultra_short_scanner import UltraShortScanner


def _ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _format_ultra_short_table(df: pd.DataFrame, top_n: int = 20) -> str:
    if df.empty:
        return "（今日未捕捉到符合条件的超短标的）\n"

    headers = ["排名", "代码", "名称", "评分", "涨幅%", "换手%", "连板", "标签"]
    rows = []
    for i, (_, row) in enumerate(df.head(top_n).iterrows(), 1):
        tags = truncate_display(str(row.get("tags", "") or ""), 30)
        rows.append([
            i,
            row["code"],
            row["name"],
            row["ultra_short_score"],
            f"{float(row['pct_chg']):.2f}",
            f"{float(row['turnover']):.2f}",
            row["consecutive_boards"],
            tags,
        ])
    return format_markdown_table(
        headers,
        rows,
        aligns=["right", "left", "left", "right", "right", "right", "right", "left"],
    )


def _cross_reference_suggestions(journal: TradeJournal, ultra_df: pd.DataFrame) -> list[str]:
    """结合交易历史与今日机会，给出交叉建议。"""
    extra: list[str] = []
    stats = journal.analyze(days=30)
    if not stats.get("has_data") or ultra_df.empty:
        return extra

    recent_codes = set(stats.get("recent_codes", []))
    top_codes = set(ultra_df.head(10)["code"].astype(str).tolist())
    overlap = recent_codes & top_codes

    if overlap:
        extra.append(
            f"你近期交易过的 {', '.join(overlap)} 出现在今日超短榜，"
            "若已持仓注意止盈/止损；未持仓避免情绪化追高。"
        )

    loss_codes = set()
    df = journal.list_trades(days=30)
    if not df.empty:
        loss_codes = set(df[df["profit_pct"] < 0]["code"].astype(str).tolist())
    repeat_loss = loss_codes & top_codes
    if repeat_loss:
        extra.append(
            f"警告：{', '.join(repeat_loss)} 曾给你带来亏损，今日再次走强。"
            "建议复盘上次失误，勿盲目重复同一错误。"
        )

    return extra


def _portfolio_holdings_suggestions(
    pm: PortfolioManager,
    ultra_df: pd.DataFrame,
    midterm: Optional[dict] = None,
) -> list[str]:
    """持仓与超短机会的交叉建议（实盘中线为主）。"""
    extra: list[str] = []
    stats = pm.analyze()
    if not stats.get("has_data"):
        return extra

    if midterm:
        extra.extend(midterm.get("optimize_suggestions", [])[:3])

    held_codes = {str(p["code"]) for p in stats["positions"]}
    if not ultra_df.empty:
        hot = ultra_df.head(10)
        held_hot = hot[hot["code"].astype(str).isin(held_codes)]
        for _, row in held_hot.iterrows():
            extra.append(
                f"持仓 {row['name']}({row['code']}) 登上超短榜（评分 {row['ultra_short_score']}），"
                f"属短线异动；中线持仓可观望不必追涨。"
            )

    extra.extend(pm.generate_suggestions())
    return extra


def _sync_portfolio_from_config() -> PortfolioManager:
    """从 data/portfolio_config.json 同步到 portfolio.json。"""
    pm = PortfolioManager()
    pm.apply_config()
    return pm


def show_portfolio(init: bool = False) -> dict:
    pm = PortfolioManager()
    if init or not pm.list_positions():
        pm = _sync_portfolio_from_config()
        print("已从 data/portfolio_config.json 同步到 data/portfolio.json\n")
    stats = pm.print_summary()
    print("\n【仓位建议】")
    for i, s in enumerate(pm.generate_suggestions(), 1):
        print(f"  {i}. {s}")
    return stats


def run_ultra_short_scan(top_prefilter: int = 300, min_score: int = 35) -> pd.DataFrame:
    print("=" * 60)
    print("超短个股捕捉")
    print("=" * 60)
    print("刷新全市场最新价...")
    get_market_spot(verbose=True, force_refresh=False)
    scanner = UltraShortScanner()
    df = scanner.scan_market(
        top_prefilter=top_prefilter,
        min_score=min_score,
        max_workers=8,
        show_progress=True,
    )
    print(f"\n捕捉完成: {len(df)} 只超短标的")
    if not df.empty:
        print("\nTOP 10:")
        cols = ["code", "name", "ultra_short_score", "pct_chg", "turnover", "tags"]
        print(df[cols].head(10).to_string(index=False))

        csv_path = OUTPUT_DIR / f"ultra_short_{datetime.now().strftime('%Y%m%d')}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n已保存: {csv_path}")
    return df


def run_learning_report(days: int = 30) -> tuple[dict, list[str]]:
    journal = TradeJournal()
    stats = journal.analyze(days=days)
    suggestions = journal.generate_suggestions(days=days)
    return stats, suggestions


def generate_daily_report(
    days: int = 30,
    top_prefilter: int = 300,
    min_score: int = 35,
) -> Path:
    _ensure_dirs()
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 60)
    print(f"每日顾问报告  {today}")
    print("=" * 60)

    # 1. 超短扫描
    ultra_df = run_ultra_short_scan(top_prefilter=top_prefilter, min_score=min_score)

    # 2. 当前仓位
    print("\n" + "=" * 60)
    print("当前持仓")
    print("=" * 60)
    pm = PortfolioManager()
    if not pm.list_positions():
        _sync_portfolio_from_config()
        pm = PortfolioManager()
    portfolio_stats = pm.print_summary()
    print("\n" + "=" * 60)
    print("实盘中线分析（复盘 / 优化 / 推荐）")
    print("=" * 60)
    midterm = run_midterm_advice(portfolio_stats, show_progress=True)
    for i, s in enumerate(midterm.get("review_summaries", []), 1):
        print(f"  复盘 {i}. {s}")
    for i, s in enumerate(midterm.get("optimize_suggestions", []), 1):
        print(f"  优化 {i}. {s}")

    ultra_records = ultra_df.head(10).to_dict("records") if not ultra_df.empty else []
    portfolio_actions = pm.generate_action_suggestions(
        ultra_short=ultra_records, midterm_advice=midterm
    )
    portfolio_suggestions = _portfolio_holdings_suggestions(pm, ultra_df, midterm)

    # 3. 学习分析
    print("\n" + "=" * 60)
    print("个人操作学习")
    print("=" * 60)
    journal = TradeJournal()
    stats, suggestions = run_learning_report(days=days)
    cross = _cross_reference_suggestions(journal, ultra_df)
    all_suggestions = portfolio_actions + portfolio_suggestions + suggestions + cross

    if stats.get("has_data"):
        print(f"\n近{days}日交易 {stats['trade_count']} 笔 | "
              f"胜率 {stats['win_rate']}% | 均收益 {stats['avg_profit_pct']}% | "
              f"均持仓 {stats['avg_hold_days']} 天")
    else:
        print("\n暂无交易记录，建议先录入历史操作。")

    print("\n【优化建议】")
    for i, s in enumerate(all_suggestions, 1):
        print(f"  {i}. {s}")

    # 3. 生成 Markdown 报告
    report_path = REPORT_DIR / f"daily_report_{datetime.now().strftime('%Y%m%d')}.md"
    md_parts = [
        f"# 每日顾问报告\n",
        f"**生成时间**: {now_str}\n",
        f"---\n",
        f"## 一、超短个股 TOP 20\n",
        _format_ultra_short_table(ultra_df, top_n=20),
        f"\n## 二、当前持仓（超短 {portfolio_stats['ultra_short_capital']/10000:.1f}万 + 中线 {portfolio_stats['midterm_capital']/10000:.1f}万 = 合计 {portfolio_stats['total_capital']/10000:.1f}万）\n\n",
    ]
    if portfolio_stats.get("has_data"):
        md_parts.append(
            f"- 超短账户: 成本 {portfolio_stats['buckets']['ultra_short']['cost']:.0f} 元 "
            f"({portfolio_stats['buckets']['ultra_short']['invested_pct']}%) | "
            f"市值 {portfolio_stats['buckets']['ultra_short']['market_value']:.0f} 元\n"
            f"- 中线账户: 成本 {portfolio_stats['buckets']['midterm']['cost']:.0f} 元 "
            f"({portfolio_stats['buckets']['midterm']['invested_pct']}%) | "
            f"市值 {portfolio_stats['buckets']['midterm']['market_value']:.0f} 元\n"
            f"- 已实现盈亏: {portfolio_stats.get('total_realized_pnl', 0):+.0f} 元 "
            f"（清盘 {portfolio_stats.get('closed_count', 0)} 笔，胜率 {portfolio_stats.get('closed_win_rate', 0)}%）\n"
            f"- 浮动盈亏: {portfolio_stats['total_float_pnl']:+.0f} 元 "
            f"({portfolio_stats['total_float_pnl_pct']:+.2f}%)\n"
            f"- 累计盈亏: {portfolio_stats.get('total_pnl', 0):+.0f} 元 "
            f"（总资金 {portfolio_stats['total_capital']:.0f} 元，"
            f"初始 {portfolio_stats.get('initial_total_capital', portfolio_stats['total_capital']):.0f} 元）\n\n"
        )
        portfolio_rows = [
            [
                p.get("bucket_label", "中线"),
                p["code"],
                p["name"],
                p["quantity"],
                f"{float(p['cost_price']):.2f}",
                f"{float(p['current_price']):.2f}",
                f"{p['profit_pct']:+.2f}",
                f"{float(p['weight_pct']):.2f}",
            ]
            for p in portfolio_stats["positions"]
        ]
        md_parts.append(
            format_markdown_table(
                ["账户", "代码", "名称", "数量", "成本", "现价", "浮盈%", "占账户%"],
                portfolio_rows,
                aligns=["left", "left", "left", "right", "right", "right", "right", "right"],
            )
        )
        closed = portfolio_stats.get("closed_positions") or []
        if closed:
            md_parts.append(f"\n### 实盘清盘记录（最近 {min(len(closed), 10)} 笔）\n\n")
            closed_rows = [
                [
                    c.get("sell_date", ""),
                    "超短" if c.get("bucket") == "ultra_short" else "中线",
                    c["code"],
                    c["name"],
                    f"{float(c['cost_price']):.3f}",
                    f"{float(c['sell_price']):.3f}",
                    f"{c.get('profit_pct', 0):+.2f}",
                    f"{c.get('profit_amount', 0):+.0f}",
                ]
                for c in closed[:10]
            ]
            md_parts.append(
                format_markdown_table(
                    ["卖出日", "账户", "代码", "名称", "成本", "卖出", "盈亏%", "盈亏额"],
                    closed_rows,
                    aligns=["left", "left", "left", "left", "right", "right", "right", "right"],
                )
            )

    if midterm.get("reviews"):
        md_parts.append(f"\n## 三、实盘中线个股复盘\n\n")
        review_rows = []
        for r in midterm["reviews"]:
            if not r.get("ok"):
                continue
            review_rows.append([
                r["code"],
                r["name"],
                r["trend"],
                r["midterm_score"],
                f"{r.get('profit_pct', 0):+.2f}",
                r["rsi"],
                r["action"],
                truncate_display(r.get("tags", ""), 20),
            ])
        if review_rows:
            md_parts.append(
                format_markdown_table(
                    ["代码", "名称", "趋势", "评分", "浮盈%", "RSI", "建议", "标签"],
                    review_rows,
                    aligns=["left", "left", "left", "right", "right", "right", "left", "left"],
                )
            )
        for s in midterm.get("review_summaries", []):
            md_parts.append(f"- {s}\n")

        level_alerts = scan_midterm_level_alerts(portfolio_stats, midterm.get("reviews"))
        if level_alerts.get("alerts"):
            md_parts.append(f"\n### 支撑/压力买卖提醒\n\n")
            alert_rows = [
                [
                    a["signal_label"],
                    a["code"],
                    truncate_display(a["name"], 8),
                    f"{a['price']:.2f}",
                    f"{a['support']:.2f}",
                    f"{a['resistance']:.2f}",
                    a["alert_label"],
                    f"{a['distance_pct']:.2f}",
                ]
                for a in level_alerts["alerts"]
            ]
            md_parts.append(
                format_markdown_table(
                    ["信号", "代码", "名称", "现价", "支撑", "压力", "提醒", "距位%"],
                    alert_rows,
                    aligns=["left", "left", "left", "right", "right", "right", "left", "right"],
                )
            )
            md_parts.append("\n")
            for i, msg in enumerate(level_alerts["messages"], 1):
                md_parts.append(f"{i}. {msg}\n")

    if midterm.get("optimize_suggestions"):
        md_parts.append(f"\n### 持仓优化\n\n")
        for i, s in enumerate(midterm["optimize_suggestions"], 1):
            md_parts.append(f"{i}. {s}\n")

    if midterm.get("recommendations"):
        md_parts.append(f"\n## 四、中线个股推荐（市值&lt;1000亿 · 股价&lt;100元）\n\n")
        rec_rows = [
            [
                r["code"],
                r["name"],
                f"{r.get('price', 0):.2f}",
                f"{r.get('market_cap_yi', 0):.1f}" if r.get("market_cap_yi") is not None else "—",
                r["midterm_score"],
                f"{r.get('pct_chg', 0):.2f}",
                r.get("rsi", ""),
                truncate_display(r.get("reason", ""), 28),
            ]
            for r in midterm["recommendations"][:8]
        ]
        md_parts.append(
            format_markdown_table(
                ["代码", "名称", "股价", "市值(亿)", "评分", "涨幅%", "RSI", "推荐理由"],
                rec_rows,
                aligns=["left", "left", "right", "right", "right", "right", "right", "left"],
            )
        )

    portfolio_review = load_latest_real_review()
    if not portfolio_review.get("has_data") and portfolio_stats.get("closed_count", 0) > 0:
        portfolio_review = run_real_portfolio_review(days=max(days, 90), show_progress=False)

    md_parts.append(f"\n## 五、实盘操作复盘（近 {portfolio_review.get('period_days', 90)} 日）\n\n")
    if portfolio_review.get("has_data"):
        prs = portfolio_review["summary"]
        md_parts.append(
            f"- 平仓 {prs['trade_count']} 笔 | 胜率 {prs['win_rate']}% | "
            f"合计 {prs['total_profit_amount']:+.0f} 元 | "
            f"均收益 {prs['avg_profit_pct']:+.2f}% | "
            f"均操作评分 {prs.get('avg_timing_score', 0)}\n\n"
        )
        if portfolio_review.get("trade_reviews"):
            pr_rows = [
                [
                    t["sell_date"],
                    t["code"],
                    truncate_display(t["name"], 8),
                    f"{t['buy_price']:.3f}",
                    f"{t['sell_price']:.3f}",
                    f"{t['profit_pct']:+.2f}",
                    str(t["hold_days"]),
                    t.get("buy_timing", ""),
                    t.get("sell_timing", ""),
                    str(t.get("timing_score", "")),
                ]
                for t in portfolio_review["trade_reviews"][:10]
            ]
            md_parts.append(
                format_markdown_table(
                    ["卖出日", "代码", "名称", "买入", "卖出", "收益%", "天数", "买点", "卖点", "评分"],
                    pr_rows,
                    aligns=["left", "left", "left", "right", "right", "right", "right", "left", "left", "right"],
                )
            )
            md_parts.append("\n")
        for i, s in enumerate(portfolio_review.get("optimization_suggestions", [])[:6], 1):
            md_parts.append(f"{i}. {s}\n")
    else:
        md_parts.append(
            "暂无清盘记录。卖出时填写卖出价同步实盘，或运行 `python daily_advisor.py review`。\n"
        )

    md_parts.append(f"\n## 六、个人绩效（近{days}日）\n")

    if stats.get("has_data"):
        md_parts.append(
            f"- 交易笔数: {stats['trade_count']}\n"
            f"- 胜率: {stats['win_rate']}%\n"
            f"- 平均收益: {stats['avg_profit_pct']}%\n"
            f"- 平均持仓: {stats['avg_hold_days']} 天\n"
            f"- 累计盈亏: {stats['total_profit_amount']} 元\n"
        )
        if stats.get("by_strategy"):
            md_parts.append("\n### 按策略统计\n\n")
            strategy_rows = [
                [row["strategy"], row["count"], row["win_rate"], row["avg_profit"], row["avg_hold"]]
                for row in stats["by_strategy"]
            ]
            md_parts.append(
                format_markdown_table(
                    ["策略", "笔数", "胜率%", "均收益%", "均持仓天"],
                    strategy_rows,
                    aligns=["left", "right", "right", "right", "right"],
                )
            )
    else:
        md_parts.append("暂无交易记录。运行 `python daily_advisor.py record` 录入。\n")

    md_parts.append(f"\n## 七、优化建议\n\n")
    for i, s in enumerate(all_suggestions, 1):
        md_parts.append(f"{i}. {s}\n")

    ai_latest = load_latest_ai_learning()
    section_tail = "八"
    if ai_latest.get("suggestions"):
        md_parts.append(f"\n## 八、AI 策略学习（第 {ai_latest.get('round', 0)} 轮）\n\n")
        md_parts.append(
            f"*引擎: {ai_latest.get('engine', 'statistical')} · "
            f"样本 {ai_latest.get('sample_count', 0)} 笔 · "
            f"{ai_latest.get('date', '')}*\n\n"
        )
        for i, s in enumerate(ai_latest["suggestions"][:8], 1):
            md_parts.append(f"{i}. {s}\n")
        if ai_latest.get("param_changes"):
            md_parts.append("\n**参数调整:** ")
            md_parts.append(", ".join(f"{k} {v}" for k, v in ai_latest["param_changes"].items()))
            md_parts.append("\n")
        section_tail = "九"

    md_parts.append(
        f"\n## {section_tail}、操作要点\n\n"
        f"1. **实盘中线**：沿 MA20 持有，跌破减仓；单票≤30%，保留 15%-25% 现金。\n"
        f"2. **涨停不破开**：10日内有涨停且收盘不破涨停日开盘价，偏强势整理。\n"
        f"3. **超短模拟**：连板龙头 + 高换手；单笔 -3% 止损，与实盘中线分开管理。\n"
        f"4. **仓位控制**：实盘以 3-6 只中线标的为主，分散行业与风格风险。\n"
        f"\n---\n*仅供参考，不构成投资建议。*\n"
    )

    report_path.write_text("".join(md_parts), encoding="utf-8")

    # JSON 摘要
    summary_path = REPORT_DIR / f"daily_summary_{datetime.now().strftime('%Y%m%d')}.json"
    summary = {
        "date": today,
        "generated_at": now_str,
        "ultra_short_count": len(ultra_df),
        "ultra_short_top10": ultra_df.head(10).to_dict("records") if not ultra_df.empty else [],
        "trade_stats": stats,
        "portfolio": portfolio_stats,
        "suggestions": all_suggestions,
        "midterm": {
            "reviews": midterm.get("reviews", []),
            "recommendations": midterm.get("recommendations", []),
            "optimize_suggestions": midterm.get("optimize_suggestions", []),
        },
        "portfolio_review": portfolio_review if portfolio_review else None,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n报告已保存:")
    print(f"  {report_path}")
    print(f"  {summary_path}")
    return report_path


def show_stats(days: int = 30) -> None:
    journal = TradeJournal()
    stats = journal.analyze(days=days)
    df = journal.list_trades(days=days)

    print("=" * 60)
    print(f"近 {days} 日交易绩效")
    print("=" * 60)

    if df.empty:
        print("暂无记录。使用: python daily_advisor.py record")
        return

    print(df[["code", "name", "buy_date", "sell_date", "profit_pct", "hold_days", "strategy"]].to_string())
    print(f"\n胜率: {stats['win_rate']}% | 均收益: {stats['avg_profit_pct']}% | "
          f"总盈亏: {stats['total_profit_amount']} 元")


def main() -> None:
    parser = argparse.ArgumentParser(description="每日顾问：超短捕捉 + 操作学习")
    parser.add_argument(
        "command",
        nargs="?",
        default="report",
        choices=[
            "report", "scan", "learn", "record", "stats", "import", "portfolio", "refresh",
            "sim", "sim-backtest", "sim-review", "sim-status", "ai-learn", "midterm", "review", "alerts", "web",
        ],
        help="sim=模拟复盘, midterm=实盘中线分析, review=实盘操作复盘, alerts=支撑压力提醒",
    )
    parser.add_argument("--days", type=int, default=30, help="学习分析回溯天数")
    parser.add_argument("--prefilter", type=int, default=300, help="超短初筛数量")
    parser.add_argument("--min-score", type=int, default=35, help="超短最低评分")
    parser.add_argument("--file", type=str, default="data/trades_template.csv", help="import 命令的 CSV 路径")
    parser.add_argument("--force", action="store_true", help="sim 命令：非 9:30-9:45 也强制选股")

    parser.add_argument("--port", type=int, default=5050, help="web 命令：监听端口")

    parser.add_argument("--init", action="store_true", help="portfolio 命令：从 portfolio_config.json 重新加载")

    args = parser.parse_args()

    try:
        if args.command == "report":
            generate_daily_report(
                days=args.days,
                top_prefilter=args.prefilter,
                min_score=args.min_score,
            )
        elif args.command == "scan":
            _ensure_dirs()
            run_ultra_short_scan(top_prefilter=args.prefilter, min_score=args.min_score)
        elif args.command == "learn":
            stats, suggestions = run_learning_report(days=args.days)
            print("【绩效】" if stats.get("has_data") else "【暂无数据】")
            if stats.get("has_data"):
                print(f"  交易 {stats['trade_count']} 笔, 胜率 {stats['win_rate']}%, "
                      f"均收益 {stats['avg_profit_pct']}%")
            print("\n【建议】")
            for i, s in enumerate(suggestions, 1):
                print(f"  {i}. {s}")
        elif args.command == "record":
            interactive_record()
        elif args.command == "stats":
            show_stats(days=args.days)
        elif args.command == "import":
            journal = TradeJournal()
            path = Path(args.file)
            if not path.exists():
                print(f"文件不存在: {path}")
                sys.exit(1)
            n = journal.import_from_csv(str(path))
            print(f"已导入 {n} 笔交易")
        elif args.command == "portfolio":
            show_portfolio(init=args.init)
        elif args.command == "refresh":
            print("=" * 60)
            print("采集全市场最新收盘/现价")
            print("=" * 60)
            quotes = collect_daily_market_close(verbose=True)
            print(f"完成，共 {len(quotes)} 只股票")
            show_portfolio(init=False)
        elif args.command == "sim":
            run_sim_daily(force=args.force)
        elif args.command == "sim-backtest":
            run_sim_backtest(days=args.days)
        elif args.command == "sim-review":
            run_sim_review()
        elif args.command == "ai-learn":
            run_ai_learning(show_progress=True, auto_apply=True)
        elif args.command == "midterm":
            stats = show_portfolio(init=args.init)
            if stats.get("has_data"):
                run_midterm_advice(stats, show_progress=True)
        elif args.command == "review":
            print("=" * 60)
            print("实盘操作复盘（买卖点分析）")
            print("=" * 60)
            result = run_real_portfolio_review(days=max(args.days, 90), show_progress=True)
            if not result.get("has_data"):
                print("\n暂无清盘/交易记录。")
            else:
                s = result["summary"]
                print(
                    f"\n平仓 {s['trade_count']} 笔 | 胜率 {s['win_rate']}% | "
                    f"合计 {s['total_profit_amount']:+.0f} 元"
                )
                print("\n【优化建议】")
                for i, sug in enumerate(result.get("optimization_suggestions", []), 1):
                    print(f"  {i}. {sug}")
                print(f"\n报告已保存至 output/real_review/")
        elif args.command == "alerts":
            from quantpy.portfolio import PortfolioManager

            print("=" * 60)
            print("中线支撑/压力买卖提醒")
            print("=" * 60)
            stats = PortfolioManager().analyze()
            if not stats.get("has_data"):
                print("\n暂无实盘持仓。")
            else:
                midterm = load_latest_midterm_advice()
                if not midterm.get("reviews"):
                    from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor
                    midterm = MidtermPortfolioAdvisor().run_quick_advice(stats)
                result = scan_midterm_level_alerts(stats, midterm.get("reviews"), save=True)
                if not result.get("alerts"):
                    print("\n暂无价位触发提醒。")
                else:
                    print(f"\n共 {result['alert_count']} 条（买入 {result['buy_count']} · 卖出 {result['sell_count']}）\n")
                    for i, msg in enumerate(result["messages"], 1):
                        print(f"  {i}. {msg}")
                    print(f"\n已保存至 output/midterm/")
        elif args.command == "sim-status":
            run_sim_status()
        elif args.command == "web":
            from quantpy.web_app import main as run_web

            run_web(port=args.port)
    except KeyboardInterrupt:
        print("\n已取消")
        sys.exit(0)


if __name__ == "__main__":
    main()
