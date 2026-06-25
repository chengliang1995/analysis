"""
中线持仓支撑/压力位提醒：接近或突破关键价位时给出买卖提示。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from quantpy.midterm_portfolio_advisor import MidtermPortfolioAdvisor
from quantpy.paths import MIDTERM_OUTPUT_DIR
from quantpy.portfolio import classify_bucket

NEAR_LEVEL_PCT = 2.5
AT_LEVEL_PCT = 1.0
BREAK_BUFFER_PCT = 0.5

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


def _format_alert_message(
    review: dict,
    alert_type: str,
    signal: str,
    level: float,
    distance_pct: float,
) -> str:
    name = review.get("name", review.get("code", ""))
    code = review.get("code", "")
    price = float(review.get("price", 0))
    action = SIGNAL_LABELS.get(signal, "观察")
    label = ALERT_LABELS.get(alert_type, alert_type)

    if alert_type in ("near_support", "at_support"):
        return (
            f"【{action}·{label}】{name}({code}) 支撑 {level:.2f}，现价 {price:.2f} "
            f"（距支撑 +{distance_pct:.2f}%），可考虑低吸/加仓"
        )
    if alert_type == "broke_support":
        return (
            f"【{action}·{label}】{name}({code}) 跌破支撑 {level:.2f}，现价 {price:.2f} "
            f"（低于支撑 {abs(distance_pct):.2f}%），建议减仓或设止损"
        )
    if alert_type in ("near_resistance", "at_resistance"):
        return (
            f"【{action}·{label}】{name}({code}) 压力 {level:.2f}，现价 {price:.2f} "
            f"（距压力 {distance_pct:.2f}%），可考虑分批止盈"
        )
    if alert_type == "broke_resistance":
        return (
            f"【{action}·{label}】{name}({code}) 突破压力 {level:.2f}，现价 {price:.2f}，"
            f"趋势偏强，可持有并沿 MA20 设止损"
        )
    return f"【{label}】{name}({code}) 现价 {price:.2f}"


def _build_alert(
    review: dict,
    alert_type: str,
    signal: str,
    severity: str,
    level: float,
    distance_pct: float,
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
        "message": _format_alert_message(review, alert_type, signal, level, distance_pct),
    }


def evaluate_review_alert(review: dict) -> Optional[dict]:
    """根据单只复盘结果判断是否触发支撑/压力提醒。"""
    if not review.get("ok"):
        return None

    price = float(review.get("price", 0))
    support = float(review.get("support", 0))
    resistance = float(review.get("resistance", 0))
    if price <= 0 or support <= 0 or resistance <= 0:
        return None

    support_alerts: List[dict] = []
    resistance_alerts: List[dict] = []

    above_support_pct = _safe_pct(price, support)
    if price < support * (1 - BREAK_BUFFER_PCT / 100):
        support_alerts.append(
            _build_alert(review, "broke_support", "sell", "high", support, above_support_pct)
        )
    elif 0 <= above_support_pct <= AT_LEVEL_PCT:
        support_alerts.append(
            _build_alert(review, "at_support", "buy", "high", support, above_support_pct)
        )
    elif above_support_pct <= NEAR_LEVEL_PCT:
        support_alerts.append(
            _build_alert(review, "near_support", "buy", "medium", support, above_support_pct)
        )

    below_resistance_pct = _safe_pct(resistance, price)
    if price > resistance * (1 + BREAK_BUFFER_PCT / 100):
        resistance_alerts.append(
            _build_alert(review, "broke_resistance", "watch", "medium", resistance, below_resistance_pct)
        )
    elif 0 <= below_resistance_pct <= AT_LEVEL_PCT:
        resistance_alerts.append(
            _build_alert(review, "at_resistance", "sell", "high", resistance, below_resistance_pct)
        )
    elif below_resistance_pct <= NEAR_LEVEL_PCT:
        resistance_alerts.append(
            _build_alert(review, "near_resistance", "sell", "medium", resistance, below_resistance_pct)
        )

    candidates = support_alerts + resistance_alerts
    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["severity_rank"], x["distance_pct"]))
    return candidates[0]


def _refresh_review_price(review: dict, current_price: float, cost_price: float = 0) -> dict:
    payload = dict(review)
    if current_price > 0:
        payload["price"] = round(current_price, 2)
        if cost_price > 0:
            payload["profit_pct"] = round(_safe_pct(current_price, cost_price), 2)
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
        }

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

    alerts: List[dict] = []
    for review in reviews:
        if not review or not review.get("ok"):
            continue
        code = str(review["code"]).zfill(6)
        fresh = _refresh_review_price(
            review,
            price_map.get(code, float(review.get("price", 0))),
            cost_map.get(code, float(review.get("cost_price", 0))),
        )
        alert = evaluate_review_alert(fresh)
        if alert:
            alerts.append(alert)

    alerts.sort(key=lambda x: (x["severity_rank"], x["distance_pct"]))

    result = {
        "generated_at": datetime.now().isoformat(),
        "has_alerts": bool(alerts),
        "alert_count": len(alerts),
        "buy_count": sum(1 for a in alerts if a["signal"] == "buy"),
        "sell_count": sum(1 for a in alerts if a["signal"] == "sell"),
        "alerts": alerts,
        "messages": [a["message"] for a in alerts],
    }

    if save:
        MIDTERM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        path = MIDTERM_OUTPUT_DIR / f"level_alerts_{day}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def load_latest_level_alerts() -> dict:
    files = sorted(MIDTERM_OUTPUT_DIR.glob("level_alerts_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
