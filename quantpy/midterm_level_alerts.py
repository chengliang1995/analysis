"""
中线持仓支撑/压力位提醒：接近或突破关键价位时给出买卖提示。
结合实盘建仓记录与近 5 个交易日走势，过滤新仓/成本区误报。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor
from quantpy.paths import MIDTERM_OUTPUT_DIR
from quantpy.portfolio import classify_bucket
from quantpy.stock_data import get_stock_recent_bars

NEAR_LEVEL_PCT = 2.5
AT_LEVEL_PCT = 1.0
BREAK_BUFFER_PCT = 0.5
NEW_POSITION_HOLD_DAYS = 5
NEW_POSITION_LOSS_TOLERANCE_PCT = -5.0
COST_NEAR_PCT = 3.0

SIGNAL_LABELS = {
    "buy": "买入",
    "sell": "卖出",
    "watch": "观察",
}

ALERT_LABELS = {
    "near_support": "接近支撑",
    "at_support": "触及支撑",
    "broke_support": "跌破支撑",
    "near_resistance": "接近压力",
    "at_resistance": "触及压力",
    "broke_resistance": "突破压力",
}

SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _safe_pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _hold_calendar_days(buy_date: str, as_of: Optional[str] = None) -> int:
    buy = str(buy_date or "")[:10]
    if not buy:
        return 999
    try:
        end = datetime.strptime((as_of or _today())[:10], "%Y-%m-%d")
        start = datetime.strptime(buy, "%Y-%m-%d")
        return max(int((end - start).days), 0)
    except ValueError:
        return 999


def _analyze_recent_trend(bars: List[dict]) -> dict:
    """近 5 个交易日走势摘要。"""
    if not bars:
        return {}
    tail = bars[-5:]
    closes = [float(b.get("close") or 0) for b in tail if b.get("close")]
    if len(closes) < 2:
        return {}

    ret_5d = _safe_pct(closes[-1], closes[0])
    pcts = [b.get("pct_chg") for b in tail if b.get("pct_chg") is not None]
    up_days = sum(1 for p in pcts if float(p) > 0)
    down_days = sum(1 for p in pcts if float(p) < 0)

    label = "震荡"
    if ret_5d >= 3:
        label = "偏强"
    elif ret_5d <= -3:
        label = "偏弱"

    today_pct = None
    if tail and tail[-1].get("pct_chg") is not None:
        today_pct = round(float(tail[-1]["pct_chg"]), 2)

    return {
        "ret_5d": round(ret_5d, 2),
        "up_days": up_days,
        "down_days": down_days,
        "trend_label": label,
        "today_pct": today_pct,
        "bars": tail,
    }


def _position_alert_context(position: dict, review: dict) -> dict:
    """结合实盘持仓成本、建仓日、现价。"""
    cost = float(position.get("cost_price", review.get("cost_price", 0)) or 0)
    buy_date = str(position.get("buy_date", ""))[:10]
    hold_days = _hold_calendar_days(buy_date)
    price = float(review.get("price", position.get("current_price", 0)) or 0)
    support = float(review.get("support", 0))
    profit_pct = (
        _safe_pct(price, cost) if cost > 0
        else float(position.get("profit_pct", review.get("profit_pct", 0)) or 0)
    )

    return {
        "cost_price": round(cost, 4) if cost else 0,
        "buy_date": buy_date,
        "hold_days": hold_days,
        "profit_pct": round(profit_pct, 2),
        "is_new_position": hold_days <= NEW_POSITION_HOLD_DAYS,
        "is_today_buy": bool(buy_date) and buy_date == _today(),
        "bought_below_support": (
            cost > 0 and support > 0 and cost <= support * 1.008
        ),
        "price_near_cost": cost > 0 and abs(profit_pct) <= COST_NEAR_PCT,
    }


def _should_suppress_alert(
    alert_type: str,
    signal: str,
    ctx: dict,
    trend: dict,
) -> Optional[str]:
    """返回抑制原因；None 表示保留提醒。"""
    profit = float(ctx.get("profit_pct", 0))

    if signal == "buy" and alert_type in ("at_support", "near_support"):
        if ctx.get("is_new_position") and ctx.get("price_near_cost"):
            return "新仓已建在支撑附近，无需重复低吸提醒"
        if ctx.get("is_today_buy"):
            return "当日已建仓，无需再买提醒"

    if signal != "sell":
        return None

    if alert_type == "broke_support":
        if ctx.get("bought_below_support") and profit > NEW_POSITION_LOSS_TOLERANCE_PCT:
            return "建仓价在支撑附近/下方，未达成本止损线"
        if ctx.get("is_new_position") and profit > -4:
            return "新仓观察期内，暂不提示跌破支撑减仓"
        if ctx.get("is_today_buy") and profit > NEW_POSITION_LOSS_TOLERANCE_PCT:
            return "当日建仓，未达止损幅度"
        if ctx.get("price_near_cost") and trend.get("trend_label") != "偏弱":
            return "现价贴近成本，近5日未明显走弱"
        bars = trend.get("bars") or []
        if len(bars) >= 2 and profit > -3:
            last2 = [
                float(b["pct_chg"]) for b in bars[-2:]
                if b.get("pct_chg") is not None
            ]
            if len(last2) == 2 and last2[0] > 0 and last2[1] > 0:
                return "近2日反弹中，暂缓卖出提醒"

    if alert_type in ("at_resistance", "near_resistance"):
        if ctx.get("is_today_buy"):
            return "当日建仓，暂不提示止盈"
        if ctx.get("is_new_position") and profit < 5:
            return "新仓浮盈有限，暂不提示压力位卖出"

    return None


def _trend_suffix(trend: dict) -> str:
    if not trend or trend.get("ret_5d") is None:
        return ""
    parts = [f"近5日{trend['ret_5d']:+.1f}%"]
    if trend.get("today_pct") is not None:
        parts.append(f"今日{trend['today_pct']:+.1f}%")
    parts.append(trend.get("trend_label", ""))
    return "（" + "，".join(p for p in parts if p) + "）"


def _format_alert_message(
    review: dict,
    alert_type: str,
    signal: str,
    level: float,
    distance_pct: float,
    *,
    ctx: Optional[dict] = None,
    trend: Optional[dict] = None,
) -> str:
    name = review.get("name", review.get("code", ""))
    code = review.get("code", "")
    price = float(review.get("price", 0))
    action = SIGNAL_LABELS.get(signal, "观察")
    label = ALERT_LABELS.get(alert_type, alert_type)
    suffix = _trend_suffix(trend or {})
    cost_note = ""
    if ctx and ctx.get("cost_price"):
        cost_note = f"，成本 {ctx['cost_price']:.2f}"

    if alert_type in ("near_support", "at_support"):
        return (
            f"【{action}·{label}】{name}({code}) 支撑 {level:.2f}，现价 {price:.2f}{cost_note} "
            f"（距支撑 +{distance_pct:.2f}%），可考虑低吸/加仓{suffix}"
        )
    if alert_type == "broke_support":
        return (
            f"【{action}·{label}】{name}({code}) 跌破支撑 {level:.2f}，现价 {price:.2f}{cost_note} "
            f"（低于支撑 {abs(distance_pct):.2f}%），建议减仓或设止损{suffix}"
        )
    if alert_type in ("near_resistance", "at_resistance"):
        return (
            f"【{action}·{label}】{name}({code}) 压力 {level:.2f}，现价 {price:.2f}{cost_note} "
            f"（距压力 {distance_pct:.2f}%），可考虑分批止盈{suffix}"
        )
    if alert_type == "broke_resistance":
        return (
            f"【{action}·{label}】{name}({code}) 突破压力 {level:.2f}，现价 {price:.2f}{cost_note}，"
            f"趋势偏强，可持有并沿 MA20 设止损{suffix}"
        )
    return f"【{label}】{name}({code}) 现价 {price:.2f}{suffix}"


def _build_alert(
    review: dict,
    alert_type: str,
    signal: str,
    severity: str,
    level: float,
    distance_pct: float,
    *,
    ctx: Optional[dict] = None,
    trend: Optional[dict] = None,
) -> dict:
    return {
        "code": str(review.get("code", "")).zfill(6),
        "name": review.get("name", ""),
        "price": round(float(review.get("price", 0)), 2),
        "support": round(float(review.get("support", 0)), 2),
        "resistance": round(float(review.get("resistance", 0)), 2),
        "ma20": round(float(review.get("ma20", 0)), 2),
        "trend": review.get("trend", ""),
        "profit_pct": round(float(review.get("profit_pct", 0)), 2),
        "alert_type": alert_type,
        "alert_label": ALERT_LABELS.get(alert_type, alert_type),
        "signal": signal,
        "signal_label": SIGNAL_LABELS.get(signal, signal),
        "severity": severity,
        "severity_rank": SEVERITY_RANK.get(severity, 9),
        "level": round(level, 2),
        "distance_pct": round(abs(distance_pct), 2),
        "message": _format_alert_message(
            review, alert_type, signal, level, distance_pct, ctx=ctx, trend=trend,
        ),
        "position_context": ctx or {},
        "recent_trend": trend or {},
    }


def evaluate_review_alert(
    review: dict,
    position: Optional[dict] = None,
    trend: Optional[dict] = None,
) -> Optional[dict]:
    """根据复盘 + 实盘持仓 + 近5日走势判断是否触发提醒。"""
    if not review.get("ok"):
        return None

    price = float(review.get("price", 0))
    support = float(review.get("support", 0))
    resistance = float(review.get("resistance", 0))
    if price <= 0 or support <= 0 or resistance <= 0:
        return None

    ctx = _position_alert_context(position or {}, review) if position else {}
    trend = trend or {}

    support_alerts: List[dict] = []
    resistance_alerts: List[dict] = []

    above_support_pct = _safe_pct(price, support)
    if price < support * (1 - BREAK_BUFFER_PCT / 100):
        support_alerts.append(
            _build_alert(review, "broke_support", "sell", "high", support, above_support_pct, ctx=ctx, trend=trend)
        )
    elif 0 <= above_support_pct <= AT_LEVEL_PCT:
        support_alerts.append(
            _build_alert(review, "at_support", "buy", "high", support, above_support_pct, ctx=ctx, trend=trend)
        )
    elif above_support_pct <= NEAR_LEVEL_PCT:
        support_alerts.append(
            _build_alert(review, "near_support", "buy", "medium", support, above_support_pct, ctx=ctx, trend=trend)
        )

    below_resistance_pct = _safe_pct(resistance, price)
    if price > resistance * (1 + BREAK_BUFFER_PCT / 100):
        resistance_alerts.append(
            _build_alert(review, "broke_resistance", "watch", "medium", resistance, below_resistance_pct, ctx=ctx, trend=trend)
        )
    elif 0 <= below_resistance_pct <= AT_LEVEL_PCT:
        resistance_alerts.append(
            _build_alert(review, "at_resistance", "sell", "high", resistance, below_resistance_pct, ctx=ctx, trend=trend)
        )
    elif below_resistance_pct <= NEAR_LEVEL_PCT:
        resistance_alerts.append(
            _build_alert(review, "near_resistance", "sell", "medium", resistance, below_resistance_pct, ctx=ctx, trend=trend)
        )

    candidates = support_alerts + resistance_alerts
    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["severity_rank"], x["distance_pct"]))
    best = candidates[0]

    if position:
        reason = _should_suppress_alert(best["alert_type"], best["signal"], ctx, trend)
        if reason:
            return None

    return best


def _refresh_review_price(review: dict, current_price: float, cost_price: float = 0) -> dict:
    payload = dict(review)
    if current_price > 0:
        payload["price"] = round(current_price, 2)
        if cost_price > 0:
            payload["profit_pct"] = round(_safe_pct(current_price, cost_price), 2)
            payload["cost_price"] = cost_price
    return payload


def scan_midterm_level_alerts(
    portfolio_stats: dict,
    reviews: Optional[List[dict]] = None,
    save: bool = False,
) -> dict:
    """扫描中线持仓，生成支撑/压力买卖提醒。"""
    positions = [
        p for p in portfolio_stats.get("positions", [])
        if p.get("bucket", classify_bucket(p.get("strategy", ""))) == "midterm"
    ]
    if not positions:
        return {
            "generated_at": datetime.now().isoformat(),
            "has_alerts": False,
            "alert_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "alerts": [],
            "messages": [],
            "suppressed_count": 0,
        }

    position_map = {str(p["code"]).zfill(6): p for p in positions}

    if reviews is None:
        reviews = MidtermPortfolioAdvisor().review_holdings(positions)
    else:
        review_map = {
            str(r["code"]).zfill(6): r
            for r in reviews
            if r.get("ok")
        }
        reviews = [
            review_map.get(str(p["code"]).zfill(6))
            for p in positions
            if review_map.get(str(p["code"]).zfill(6))
        ]

    price_map = {
        str(p["code"]).zfill(6): float(p.get("current_price", 0))
        for p in positions
    }
    cost_map = {
        str(p["code"]).zfill(6): float(p.get("cost_price", 0))
        for p in positions
    }

    trend_cache: Dict[str, dict] = {}
    for code in position_map:
        bars = get_stock_recent_bars(code, days=5)
        trend_cache[code] = _analyze_recent_trend(bars)

    alerts: List[dict] = []
    suppressed = 0
    for review in reviews:
        if not review or not review.get("ok"):
            continue
        code = str(review["code"]).zfill(6)
        position = position_map.get(code, {})
        fresh = _refresh_review_price(
            review,
            price_map.get(code, float(review.get("price", 0))),
            cost_map.get(code, float(review.get("cost_price", 0))),
        )

        trend = trend_cache.get(code, {})
        raw = _evaluate_raw_alerts(fresh)
        alert = evaluate_review_alert(fresh, position=position, trend=trend)
        if alert:
            alerts.append(alert)
        elif raw:
            suppressed += 1

    alerts.sort(key=lambda x: (x["severity_rank"], x["distance_pct"]))

    result = {
        "generated_at": datetime.now().isoformat(),
        "has_alerts": bool(alerts),
        "alert_count": len(alerts),
        "buy_count": sum(1 for a in alerts if a["signal"] == "buy"),
        "sell_count": sum(1 for a in alerts if a["signal"] == "sell"),
        "suppressed_count": suppressed,
        "alerts": alerts,
        "messages": [a["message"] for a in alerts],
    }

    if save:
        MIDTERM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        path = MIDTERM_OUTPUT_DIR / f"level_alerts_{day}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def _evaluate_raw_alerts(review: dict) -> List[dict]:
    """仅按技术位生成候选（不应用实盘过滤）。"""
    price = float(review.get("price", 0))
    support = float(review.get("support", 0))
    resistance = float(review.get("resistance", 0))
    if price <= 0 or support <= 0 or resistance <= 0:
        return []

    out: List[dict] = []
    above_support_pct = _safe_pct(price, support)
    if price < support * (1 - BREAK_BUFFER_PCT / 100):
        out.append({"alert_type": "broke_support", "signal": "sell", "severity_rank": 0, "distance_pct": abs(above_support_pct)})
    elif 0 <= above_support_pct <= AT_LEVEL_PCT:
        out.append({"alert_type": "at_support", "signal": "buy", "severity_rank": 0, "distance_pct": abs(above_support_pct)})
    elif above_support_pct <= NEAR_LEVEL_PCT:
        out.append({"alert_type": "near_support", "signal": "buy", "severity_rank": 1, "distance_pct": abs(above_support_pct)})

    below_resistance_pct = _safe_pct(resistance, price)
    if price > resistance * (1 + BREAK_BUFFER_PCT / 100):
        out.append({"alert_type": "broke_resistance", "signal": "watch", "severity_rank": 1, "distance_pct": abs(below_resistance_pct)})
    elif 0 <= below_resistance_pct <= AT_LEVEL_PCT:
        out.append({"alert_type": "at_resistance", "signal": "sell", "severity_rank": 0, "distance_pct": abs(below_resistance_pct)})
    elif below_resistance_pct <= NEAR_LEVEL_PCT:
        out.append({"alert_type": "near_resistance", "signal": "sell", "severity_rank": 1, "distance_pct": abs(below_resistance_pct)})

    out.sort(key=lambda x: (x["severity_rank"], x["distance_pct"]))
    return out


def load_latest_level_alerts() -> dict:
    files = sorted(MIDTERM_OUTPUT_DIR.glob("level_alerts_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
