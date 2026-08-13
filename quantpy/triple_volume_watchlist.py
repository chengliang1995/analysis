"""
三倍量「一阳穿三线」观察池：突破日仅入池（不买入），5 个交易日内
缩量至突破前水平且站稳 MA5 时触发买入信号。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from quantpy.json_util import sanitize_for_json
from quantpy.midterm_pick_tracker import _trading_days_between
from quantpy.midterm_triple_volume_selector import _align_volume_unit
from quantpy.paths import MIDTERM_OUTPUT_DIR, TRIPLE_VOLUME_WATCHLIST_FILE
from quantpy.stock_data import get_stock_hist

WATCH_MIN_DAYS = 1
WATCH_MAX_DAYS = 5
MA5_HOLD_DAYS = 2
MA5_TOLERANCE = 0.005
# 当日量相对突破前量的上限（1.0 = 须缩至突破前及以下，略放 5% 容差对齐单位）
PRE_VOLUME_MAX_RATIO = 1.05
MAX_ITEMS = 200
# 同步近期选股报告的日历日窗口（覆盖观察期 + 缓冲）
INGEST_LOOKBACK_DAYS = 20
# 观察结束胜率：结算价相对突破价收益超过 5% 记为赢
WIN_THRESHOLD_PCT = 5.0
COMPLETED_STATUSES = frozenset({"buy_signal", "expired"})


def _default_state() -> dict:
    return {
        "version": 1,
        "watch_min_days": WATCH_MIN_DAYS,
        "watch_max_days": WATCH_MAX_DAYS,
        "items": [],
        "buy_alerts": [],
        "last_record_date": "",
        "last_evaluated": "",
        "summary": {},
    }


def _load_state() -> dict:
    if not TRIPLE_VOLUME_WATCHLIST_FILE.exists():
        return _default_state()
    try:
        data = json.loads(TRIPLE_VOLUME_WATCHLIST_FILE.read_text(encoding="utf-8"))
        base = _default_state()
        base.update(data)
        base["watch_min_days"] = WATCH_MIN_DAYS
        base["watch_max_days"] = WATCH_MAX_DAYS
        return base
    except (OSError, json.JSONDecodeError):
        return _default_state()


def _save_state(state: dict) -> None:
    TRIPLE_VOLUME_WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRIPLE_VOLUME_WATCHLIST_FILE.write_text(
        json.dumps(sanitize_for_json(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _trading_days_since(pick_date: str, ref_date: Optional[str] = None) -> int:
    ref_date = ref_date or _today()
    cal = _trading_days_between(pick_date, ref_date)
    if not cal:
        return 0
    return max(0, len(cal) - 1)


def _watch_snapshot(rec: dict, pick_date: str) -> dict:
    return {
        "code": str(rec.get("code", "")).zfill(6),
        "name": str(rec.get("name", "")),
        "pick_date": pick_date,
        "pick_price": float(rec.get("price") or 0),
        "pick_volume": int(rec.get("today_volume") or 0),
        "pre_pick_volume": int(rec.get("yesterday_volume") or 0),
        "volume_ratio": float(rec.get("volume_ratio") or 0),
        "midterm_score": float(rec.get("midterm_score") or 0),
        "ma5_at_pick": float(rec.get("ma5") or 0),
        "industry": rec.get("industry", ""),
        "status": "watching",
        "days_watched": 0,
        "buy_signal_date": "",
        "buy_signal_price": None,
        "buy_reason": "",
        "last_check_date": "",
        "last_price": None,
        "last_ma5": None,
        "last_volume_ratio": None,
        "ma5_hold_ok": False,
        "volume_shrink_ok": False,
        "settle_price": None,
        "return_pct": None,
        "is_win": None,
        "completed_at": "",
    }


def add_to_watchlist(
    recommendations: List[dict],
    *,
    pick_date: Optional[str] = None,
    show_progress: bool = False,
) -> dict:
    """将三倍量选股结果加入观察池。"""
    if not recommendations:
        return {"added": 0, "message": "无推荐标的"}

    pick_date = pick_date or _today()
    state = _load_state()
    existing = {
        (it["pick_date"], it["code"])
        for it in state.get("items", [])
        if it.get("status") in ("watching", "buy_signal")
    }

    added: List[dict] = []
    for rec in recommendations:
        code = str(rec.get("code", "")).zfill(6)
        if not code or code == "000000":
            continue
        if (pick_date, code) in existing:
            continue
        if float(rec.get("price") or 0) <= 0:
            continue
        added.append(_watch_snapshot(rec, pick_date))

    if not added:
        if show_progress:
            print(f"  观察池：{pick_date} 已记录或无新标的")
        return {"added": 0, "pick_date": pick_date, "message": "当日已记录"}

    items = state.setdefault("items", [])
    items.extend(added)
    state["items"] = _trim_items(items)
    prev = str(state.get("last_record_date") or "")
    state["last_record_date"] = max(prev, pick_date) if prev else pick_date
    state["summary"] = _compute_summary(state)
    _save_state(state)

    if show_progress:
        names = "、".join(r["name"] for r in added[:5])
        print(f"  观察池：新增 {len(added)} 只（{pick_date}）{names}")

    return {
        "added": len(added),
        "pick_date": pick_date,
        "codes": [r["code"] for r in added],
    }


def _pick_date_from_report(path: Path, data: dict) -> str:
    stem = path.stem.replace("triple_volume_", "")
    if len(stem) == 8 and stem.isdigit():
        return f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
    gen = str(data.get("generated_at") or "")
    if len(gen) >= 10 and gen[4] == "-" and gen[7] == "-":
        return gen[:10]
    return _today()


def ingest_from_select_reports(
    *,
    lookback_days: int = INGEST_LOOKBACK_DAYS,
    show_progress: bool = False,
) -> dict:
    """将近期每日三倍量选股报告中的命中标的同步进观察池。"""
    root = MIDTERM_OUTPUT_DIR
    if not root.exists():
        return {"added": 0, "files": 0, "dates": []}

    cutoff = (_today_dt() - timedelta(days=max(1, lookback_days))).strftime("%Y-%m-%d")
    files = sorted(root.glob("triple_volume_*.json"))
    total_added = 0
    synced_dates: List[str] = []
    used_files = 0

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pick_date = _pick_date_from_report(path, data)
        if pick_date < cutoff:
            continue
        recs = data.get("recommendations") or []
        if not recs:
            continue
        used_files += 1
        result = add_to_watchlist(recs, pick_date=pick_date, show_progress=False)
        added = int(result.get("added") or 0)
        if added:
            total_added += added
            synced_dates.append(pick_date)
            if show_progress:
                print(f"  观察池：同步 {pick_date} 选股报告，新增 {added} 只")

    if show_progress and not total_added and used_files:
        print(f"  观察池：近期 {used_files} 份选股报告已全部入池")

    return {
        "added": total_added,
        "files": used_files,
        "dates": synced_dates,
    }


def _today_dt() -> datetime:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _trim_items(items: List[dict]) -> List[dict]:
    """优先保留已结束样本，避免被新观察挤掉无法回看胜率。"""
    if len(items) <= MAX_ITEMS:
        return items
    completed = [i for i in items if i.get("status") in COMPLETED_STATUSES]
    watching = [i for i in items if i.get("status") not in COMPLETED_STATUSES]
    keep_completed = max(40, MAX_ITEMS // 2)
    keep_watching = MAX_ITEMS - min(len(completed), keep_completed)
    return completed[-keep_completed:] + watching[-max(0, keep_watching):]


def _hist_close_on_or_before(hist: pd.DataFrame, ref_date: str) -> Optional[float]:
    if hist is None or hist.empty or "close" not in hist.columns:
        return None
    close = pd.to_numeric(hist["close"], errors="coerce")
    if "date" not in hist.columns:
        val = float(close.iloc[-1]) if len(close) else 0.0
        return val if val > 0 else None
    dates = hist["date"].astype(str).str[:10]
    mask = dates <= ref_date
    if not mask.any():
        val = float(close.iloc[-1]) if len(close) else 0.0
        return val if val > 0 else None
    val = float(close[mask].iloc[-1])
    return val if val > 0 else None


def _settle_return(item: dict, settle_price: Optional[float]) -> dict:
    """按突破价结算收益，写入胜负标记。"""
    item = dict(item)
    pick = float(item.get("pick_price") or 0)
    price = float(settle_price or 0)
    if pick <= 0 or price <= 0:
        return item
    ret = (price / pick - 1.0) * 100.0
    item["settle_price"] = round(price, 2)
    item["last_price"] = round(price, 2)
    item["return_pct"] = round(ret, 2)
    item["is_win"] = bool(ret > WIN_THRESHOLD_PCT)
    if not item.get("completed_at"):
        item["completed_at"] = item.get("buy_signal_date") or item.get("last_check_date") or _today()
    return item


def _ensure_settled(item: dict, ref_date: str) -> dict:
    """已结束样本若缺收益，补算结算价与胜率字段。"""
    if item.get("status") not in COMPLETED_STATUSES:
        return item
    if item.get("return_pct") is not None and item.get("settle_price"):
        return item

    settle = None
    if item.get("status") == "buy_signal" and item.get("buy_signal_price"):
        settle = float(item["buy_signal_price"])
    elif item.get("last_price"):
        settle = float(item["last_price"])
    else:
        hist = get_stock_hist(item["code"], days=40, patch_live=True)
        settle = _hist_close_on_or_before(hist, ref_date)

    return _settle_return(item, settle)


def _check_ma5_hold(hist: pd.DataFrame) -> tuple[bool, float, float]:
    close = pd.to_numeric(hist["close"], errors="coerce")
    ma5 = close.rolling(5).mean()
    if len(close) < MA5_HOLD_DAYS + 5:
        return False, 0.0, 0.0

    curr_close = float(close.iloc[-1])
    curr_ma5 = float(ma5.iloc[-1])
    if curr_ma5 <= 0 or curr_close < curr_ma5 * (1 - MA5_TOLERANCE):
        return False, curr_close, curr_ma5

    for i in range(-MA5_HOLD_DAYS, 0):
        c = float(close.iloc[i])
        m = float(ma5.iloc[i])
        if m <= 0 or c < m * (1 - MA5_TOLERANCE):
            return False, curr_close, curr_ma5
    return True, curr_close, curr_ma5


def _resolve_pre_pick_volume(item: dict) -> float:
    """突破前一日成交量（缩量对比基准）。"""
    pre = float(item.get("pre_pick_volume") or 0)
    if pre > 0:
        return pre
    pick_vol = float(item.get("pick_volume") or 0)
    vr = float(item.get("volume_ratio") or 0)
    if pick_vol > 0 and vr > 0:
        return pick_vol / vr
    return 0.0


def _check_volume_shrink(hist: pd.DataFrame, pre_pick_volume: float) -> tuple[bool, float]:
    """当日量须缩至突破前水平（≤ pre_pick_volume）。"""
    if pre_pick_volume <= 0:
        return False, 0.0
    volume = pd.to_numeric(hist.get("volume", 0), errors="coerce").fillna(0)
    if len(volume) < 1:
        return False, 0.0
    today_vol = float(volume.iloc[-1])
    today_vol = _align_volume_unit(today_vol, pre_pick_volume)
    ratio = today_vol / pre_pick_volume
    ok = ratio <= PRE_VOLUME_MAX_RATIO
    return ok, round(ratio, 3)


def _evaluate_item(item: dict, ref_date: str) -> dict:
    code = item["code"]
    pick_date = item["pick_date"]
    days = _trading_days_since(pick_date, ref_date)

    item = dict(item)
    item["days_watched"] = days
    item["last_check_date"] = ref_date

    if item.get("status") == "buy_signal":
        if days <= WATCH_MAX_DAYS:
            item = dict(item)
            item["status"] = "watching"
            item.pop("buy_signal_date", None)
            item.pop("buy_signal_price", None)
            item.pop("completed_at", None)
            item.pop("settle_price", None)
            item.pop("return_pct", None)
            item.pop("is_win", None)
        else:
            return _ensure_settled(item, ref_date)

    if item.get("status") == "expired":
        return _ensure_settled(item, ref_date)

    if days > WATCH_MAX_DAYS:
        item["status"] = "expired"
        item["buy_reason"] = f"观察期满（>{WATCH_MAX_DAYS}交易日）未触发缩量站稳MA5买点"
        item["completed_at"] = ref_date
        hist = get_stock_hist(code, days=40, patch_live=True)
        settle = _hist_close_on_or_before(hist, ref_date)
        if settle:
            item = _settle_return(item, settle)
            item["buy_reason"] = (
                f"观察期满未买入 · 相对突破价 {item.get('return_pct', 0):+.2f}%"
            )
        return item

    if days < WATCH_MIN_DAYS:
        item["status"] = "watching"
        item["buy_reason"] = f"突破日入池（第{days}日），5日内等缩量站稳MA5"
        return item

    hist = get_stock_hist(code, days=40, patch_live=True)
    if hist.empty or len(hist) < 22:
        item["buy_reason"] = "K线数据不足，继续观察"
        return item

    ma5_ok, price, ma5 = _check_ma5_hold(hist)
    pre_vol = _resolve_pre_pick_volume(item)
    if pre_vol > 0 and not item.get("pre_pick_volume"):
        item["pre_pick_volume"] = int(pre_vol)
    shrink_ok, vol_ratio = _check_volume_shrink(hist, pre_vol)
    item["last_price"] = round(price, 2)
    item["last_ma5"] = round(ma5, 2)
    item["last_volume_ratio"] = vol_ratio
    item["ma5_hold_ok"] = ma5_ok
    item["volume_shrink_ok"] = shrink_ok

    if ma5_ok and shrink_ok:
        item["status"] = "buy_signal"
        item["buy_signal_date"] = ref_date
        item["buy_signal_price"] = round(price, 2)
        item["completed_at"] = ref_date
        item = _settle_return(item, price)
        item["buy_reason"] = (
            f"站稳MA5（近{MA5_HOLD_DAYS}日）+ 缩量至突破前量{vol_ratio:.0%}，"
            f"触发买入（相对突破价 {item.get('return_pct', 0):+.2f}%）"
        )
    else:
        item["status"] = "watching"
        parts = []
        if not ma5_ok:
            parts.append("未站稳MA5")
        if not shrink_ok:
            parts.append(f"量能未缩至突破前（{vol_ratio:.0%} vs 突破前）")
        item["buy_reason"] = f"观察中（第{days}日）：{' · '.join(parts)}"

    return item


def evaluate_watchlist(*, show_progress: bool = False) -> dict:
    """评估观察池，生成买入提示。"""
    ref_date = _today()
    state = _load_state()
    items = state.get("items", [])
    if not items:
        return {"evaluated": 0, "buy_signals": 0, "expired": 0, "watching": 0}

    updated: List[dict] = []
    new_alerts: List[dict] = []
    buy_count = 0
    expired_count = 0
    watching_count = 0

    for raw in items:
        prev_status = raw.get("status")
        item = _evaluate_item(raw, ref_date)
        status = item.get("status")
        if status == "buy_signal":
            buy_count += 1
            if prev_status != "buy_signal":
                new_alerts.append({
                    "code": item["code"],
                    "name": item["name"],
                    "pick_date": item["pick_date"],
                    "signal_date": ref_date,
                    "price": item.get("buy_signal_price"),
                    "pick_price": item.get("pick_price"),
                    "volume_ratio": item.get("last_volume_ratio"),
                    "reason": item.get("buy_reason", ""),
                })
        elif status == "expired":
            expired_count += 1
        elif status == "watching":
            watching_count += 1
        updated.append(item)

    alerts = state.get("buy_alerts", [])
    if new_alerts:
        alerts = new_alerts + alerts
    alerts = alerts[:50]

    state["items"] = _trim_items(updated)
    state["buy_alerts"] = alerts
    state["last_evaluated"] = ref_date
    state["summary"] = _compute_summary(state)
    _save_state(state)

    if show_progress:
        summary = state["summary"]
        print(
            f"  观察池评估：买入信号 {buy_count} · 观察中 {watching_count} · 已过期 {expired_count}"
            f" · 已结束胜率 {summary.get('win_rate', 0)}%"
            f"（{summary.get('settled_count', 0)} 只结算 · 均收益 {summary.get('avg_return', 0):+.2f}%）"
        )
        for a in new_alerts[:5]:
            print(f"    ★ {a['name']}({a['code']}) {a['reason']}")

    return {
        "evaluated": len(updated),
        "buy_signals": buy_count,
        "new_buy_signals": len(new_alerts),
        "watching": watching_count,
        "expired": expired_count,
        "alerts": new_alerts,
    }


def _compute_summary(state: dict) -> dict:
    items = state.get("items", [])
    watching = [i for i in items if i.get("status") == "watching"]
    buy_signal = [i for i in items if i.get("status") == "buy_signal"]
    expired = [i for i in items if i.get("status") == "expired"]
    completed = [i for i in items if i.get("status") in COMPLETED_STATUSES]
    settled = [i for i in completed if i.get("return_pct") is not None]
    wins = [i for i in settled if i.get("is_win")]
    avg_return = (
        round(sum(float(i.get("return_pct") or 0) for i in settled) / len(settled), 2)
        if settled else 0.0
    )
    win_rate = round(len(wins) / len(settled) * 100, 1) if settled else 0.0
    buy_wins = [
        i for i in buy_signal
        if i.get("return_pct") is not None and i.get("is_win")
    ]
    buy_settled = [i for i in buy_signal if i.get("return_pct") is not None]
    signal_win_rate = (
        round(len(buy_wins) / len(buy_settled) * 100, 1) if buy_settled else 0.0
    )
    return {
        "total": len(items),
        "watching_count": len(watching),
        "buy_signal_count": len(buy_signal),
        "expired_count": len(expired),
        "completed_count": len(completed),
        "settled_count": len(settled),
        "win_rate": win_rate,
        "signal_win_rate": signal_win_rate,
        "avg_return": avg_return,
        "win_threshold_pct": WIN_THRESHOLD_PCT,
        "last_record_date": state.get("last_record_date", ""),
        "last_evaluated": state.get("last_evaluated", ""),
    }


def load_watchlist_summary(*, evaluate: bool = False) -> dict:
    """加载观察池摘要（仪表盘）。"""
    if evaluate:
        evaluate_watchlist(show_progress=False)
    state = _load_state()
    items = state.get("items", [])
    watching = [i for i in items if i.get("status") == "watching"]
    buy_signals = [i for i in items if i.get("status") == "buy_signal"]
    expired = [i for i in items if i.get("status") == "expired"]
    completed = sorted(
        [i for i in items if i.get("status") in COMPLETED_STATUSES],
        key=lambda x: (
            str(x.get("completed_at") or x.get("last_check_date") or ""),
            str(x.get("pick_date") or ""),
        ),
        reverse=True,
    )
    summary = state.get("summary") or _compute_summary(state)
    # 若摘要缺胜率字段（旧数据），即时重算
    if "win_rate" not in summary or "completed_count" not in summary:
        summary = _compute_summary(state)
        state["summary"] = summary
        _save_state(state)
    return {
        "summary": summary,
        "watching": watching,
        "buy_signals": buy_signals,
        "expired": expired,
        "completed": completed,
        "recent_alerts": (state.get("buy_alerts") or [])[:15],
        "rules": {
            "watch_days": f"突破后{WATCH_MAX_DAYS}交易日内",
            "ma5_hold": f"近{MA5_HOLD_DAYS}日收盘站稳MA5",
            "volume_shrink": f"当日量≤突破前量×{PRE_VOLUME_MAX_RATIO:.0%}",
            "win_rule": f"结束观察后相对突破价收益>{WIN_THRESHOLD_PCT:g}% 记胜",
            "buy_rule": "突破日不入池买入，仅观察池触发买点",
        },
    }


def watch_item_to_buy_recommendation(item: dict, *, ref_price: Optional[float] = None) -> dict:
    """将观察池买入信号转为推荐条目；ref_price 为当日成交价，缺省用 last_price。"""
    price = float(ref_price if ref_price is not None else (item.get("last_price") or 0))
    if price <= 0:
        price = float(item.get("buy_signal_price") or 0)
    pick_date = str(item.get("pick_date") or "")
    signal_date = str(item.get("buy_signal_date") or "")
    return {
        "code": str(item.get("code", "")).zfill(6),
        "name": str(item.get("name", "")),
        "price": round(price, 2),
        "midterm_score": float(item.get("midterm_score") or 80),
        "reason": str(item.get("buy_reason") or "观察池：缩量站稳MA5")[:200],
        "tags": f"观察池买入,突破{pick_date},信号{signal_date},缩量站稳MA5",
        "industry": item.get("industry", ""),
        "pick_date": pick_date,
        "buy_signal_date": signal_date,
        "breakout_price": float(item.get("pick_price") or 0),
        "volume_ratio": item.get("volume_ratio"),
    }


def get_buy_signal_recommendations(
    *,
    ref_date: Optional[str] = None,
    today_only: bool = True,
) -> List[dict]:
    """返回观察池已触发的买入信号（非突破日标的）。"""
    ref_date = ref_date or _today()
    state = _load_state()
    out: List[dict] = []
    for item in state.get("items", []):
        if item.get("status") != "buy_signal":
            continue
        sig_date = str(item.get("buy_signal_date") or "")[:10]
        if today_only and sig_date != ref_date:
            continue
        # 推荐价用 last_price（最近评估价）；模拟买入会再拉当日行情覆盖
        price = float(item.get("last_price") or item.get("buy_signal_price") or 0)
        if price <= 0:
            continue
        out.append(watch_item_to_buy_recommendation(item, ref_price=price))
    out.sort(key=lambda x: float(x.get("midterm_score") or 0), reverse=True)
    return out


def run_watchlist_cycle(
    recommendations: Optional[List[dict]] = None,
    *,
    pick_date: Optional[str] = None,
    show_progress: bool = False,
    sync_reports: bool = True,
) -> dict:
    """完整周期：同步每日选股入池 → 评估买入信号。"""
    sync = {"added": 0, "files": 0, "dates": []}
    if sync_reports:
        sync = ingest_from_select_reports(show_progress=show_progress)

    record = {"added": 0}
    if recommendations:
        record = add_to_watchlist(
            recommendations,
            pick_date=pick_date,
            show_progress=show_progress,
        )

    eval_result = evaluate_watchlist(show_progress=show_progress)
    payload = load_watchlist_summary(evaluate=False)
    payload["sync"] = sync
    payload["record"] = {
        "added": int(sync.get("added") or 0) + int(record.get("added") or 0),
        "from_reports": int(sync.get("added") or 0),
        "from_live": int(record.get("added") or 0),
        "pick_date": record.get("pick_date") or (sync.get("dates") or [None])[-1],
        "codes": list(record.get("codes") or []),
    }
    payload["eval"] = eval_result
    return payload


def sync_and_evaluate_watchlist(*, show_progress: bool = False) -> dict:
    """观察池入口：先把每日选股报告入池，再评估。"""
    return run_watchlist_cycle(show_progress=show_progress, sync_reports=True)
