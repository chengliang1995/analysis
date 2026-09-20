"""Serenity 卡脖子选股：独立于中线/超短/热门板块推荐。

落盘：
- output/serenity/serenity_*.json + serenity_latest.json
- data/serenity_choke_picks.json（跟进；勿写入 midterm_pick_tracker）

strategy_id 固定为 serenity_choke。
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from quantpy.json_util import sanitize_for_json
from quantpy.paths import OUTPUT_DIR, SERENITY_PICKS_FILE
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import (
    fetch_board_constituents,
    fetch_board_list,
    get_market_spot,
    get_stock_hist,
    is_bse_code,
)

STRATEGY_ID = "serenity_choke"
SERENITY_OUTPUT_DIR = OUTPUT_DIR / "serenity"
FOLLOW_TRADING_DAYS = 10
WIN_THRESHOLD_PCT = 3.0
MIN_AMOUNT_YI = 0.5  # 日成交额下限（亿元）流动性地板
MAX_CANDIDATES = 12
HIST_WORKERS = 6


def _progress(msg: str, show: bool) -> None:
    if show:
        print(msg, flush=True)


def _default_picks_state() -> dict:
    return {
        "version": 1,
        "strategy_id": STRATEGY_ID,
        "follow_trading_days": FOLLOW_TRADING_DAYS,
        "win_threshold_pct": WIN_THRESHOLD_PCT,
        "records": [],
        "batches": [],
        "last_run": "",
        "summary": {},
    }


def _load_picks() -> dict:
    if not SERENITY_PICKS_FILE.exists():
        return _default_picks_state()
    try:
        data = json.loads(SERENITY_PICKS_FILE.read_text(encoding="utf-8"))
        base = _default_picks_state()
        base.update(data)
        base["strategy_id"] = STRATEGY_ID
        return base
    except (OSError, json.JSONDecodeError):
        return _default_picks_state()


def _save_picks(state: dict) -> None:
    SERENITY_PICKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SERENITY_PICKS_FILE.write_text(
        json.dumps(sanitize_for_json(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_theme(theme: str) -> str:
    return re.sub(r"\s+", "", str(theme or "").strip())


def _match_boards(theme: str, boards: List[dict], limit: int = 5) -> List[dict]:
    """按主题名模糊匹配板块；无命中则取热度前列供人工对照。"""
    key = _normalize_theme(theme).lower()
    if not boards:
        return []
    scored: List[Tuple[float, dict]] = []
    for b in boards:
        name = str(b.get("name") or "")
        code = str(b.get("code") or "")
        n = _normalize_theme(name).lower()
        score = 0.0
        if key and (key in n or n in key):
            score += 100
        # 单字过短不拆；两字及以上子串
        if len(key) >= 2:
            for i in range(len(key) - 1):
                if key[i : i + 2] in n:
                    score += 8
        score += float(b.get("board_score") or 0) * 0.01
        score += float(b.get("pct_chg") or 0) * 0.1
        if score > 0:
            scored.append((score, b))
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return [b for _, b in scored[:limit]]
    # 无文本命中：返回热门前列，并标记为 fallback
    hot = sorted(boards, key=lambda x: float(x.get("board_score") or 0), reverse=True)
    out = []
    for b in hot[:limit]:
        item = dict(b)
        item["match_mode"] = "hot_fallback"
        out.append(item)
    return out


def _ma_structure(hist: pd.DataFrame) -> Dict[str, Any]:
    if hist is None or hist.empty or "close" not in hist.columns:
        return {"label": "unknown", "rsi": None}
    close = pd.to_numeric(hist["close"], errors="coerce").dropna()
    if len(close) < 25:
        return {"label": "unknown", "rsi": None}
    ma5 = float(close.rolling(5).mean().iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else None
    price = float(close.iloc[-1])
    if ma60 is not None and price >= ma20 >= ma60:
        label = "多头"
    elif ma60 is not None and price < ma20 <= ma60:
        label = "空头"
    else:
        label = "纠缠"
    # 简易 RSI
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    last_loss = float(loss.iloc[-1]) if pd.notna(loss.iloc[-1]) else 0.0
    last_gain = float(gain.iloc[-1]) if pd.notna(gain.iloc[-1]) else 0.0
    if last_loss == 0:
        rsi = 100.0 if last_gain > 0 else 50.0
    else:
        rs = last_gain / last_loss
        rsi = float(100 - 100 / (1 + rs))
    chase = bool(ma20 > 0 and price / ma20 > 1.08)
    return {
        "label": label,
        "rsi": round(rsi, 1),
        "price": round(price, 2),
        "ma20": round(ma20, 2),
        "ma5": round(ma5, 2),
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "chase_ma20": chase,
    }


def _enrich_spot(codes: List[str], spot: pd.DataFrame) -> Dict[str, dict]:
    if spot is None or spot.empty:
        return {}
    df = spot.copy()
    if "code" not in df.columns:
        return {}
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].isin(codes)]
    out: Dict[str, dict] = {}
    for _, row in df.iterrows():
        code = str(row["code"]).zfill(6)
        amount = row.get("amount")
        try:
            amount_f = float(amount) if amount is not None and pd.notna(amount) else None
        except (TypeError, ValueError):
            amount_f = None
        # amount 可能是元；转亿元
        amount_yi = None
        if amount_f is not None and amount_f > 0:
            amount_yi = amount_f / 1e8 if amount_f > 1e6 else amount_f
        mcap = row.get("market_cap")
        try:
            mcap_f = float(mcap) if mcap is not None and pd.notna(mcap) else None
        except (TypeError, ValueError):
            mcap_f = None
        out[code] = {
            "price": row.get("price") or row.get("close"),
            "pct_chg": row.get("pct_chg") if "pct_chg" in row else row.get("changepercent"),
            "turnover": row.get("turnover"),
            "amount_yi": round(amount_yi, 2) if amount_yi is not None else None,
            "market_cap": round(mcap_f, 2) if mcap_f is not None else None,
        }
    return out


def _score_candidate(member: dict, spot_row: dict, structure: dict) -> Optional[dict]:
    name = str(member.get("name") or "")
    if "ST" in name.upper() or "退" in name:
        return None
    code = str(member.get("code") or "").zfill(6)
    if is_bse_code(code):
        return None

    pct = float(spot_row.get("pct_chg") if spot_row.get("pct_chg") is not None else member.get("pct_chg") or 0)
    turnover = float(
        spot_row.get("turnover") if spot_row.get("turnover") is not None else member.get("turnover") or 0
    )
    amount_yi = spot_row.get("amount_yi")
    mcap = spot_row.get("market_cap")

    # 流动性地板
    if amount_yi is not None and amount_yi < MIN_AMOUNT_YI and turnover < 1.0:
        return None

    tags: List[str] = []
    score = 40.0

    # 小盘弹性偏好（市值亿元；无市值则中性）
    if mcap is not None:
        if mcap <= 80:
            score += 18
            tags.append("小盘弹性")
        elif mcap <= 200:
            score += 10
            tags.append("中小盘")
        elif mcap >= 800:
            score -= 8
            tags.append("大盘降权")

    # 不过度追涨（卡脖子偏布局，非连板情绪票）
    if pct >= 9.5:
        score -= 12
        tags.append("涨停降权")
    elif 2 <= pct <= 7:
        score += 6
        tags.append("温和走强")

    if turnover >= 3:
        score += 4
    if amount_yi is not None and amount_yi >= 2:
        score += 4
        tags.append("流动性尚可")

    label = structure.get("label") or "unknown"
    if label == "多头":
        score += 8
        tags.append("均线多头")
    elif label == "空头":
        score -= 10
        tags.append("均线空头")
    if structure.get("chase_ma20"):
        score -= 6
        tags.append("MA20追高")

    return {
        "code": code,
        "name": name,
        "strategy_id": STRATEGY_ID,
        "pct_chg": round(pct, 2),
        "turnover": round(turnover, 2),
        "amount_yi": amount_yi,
        "market_cap": mcap,
        "price": spot_row.get("price") or member.get("price"),
        "ma_structure": label,
        "rsi": structure.get("rsi"),
        "chase_ma20": bool(structure.get("chase_ma20")),
        "score": round(score, 1),
        "tags": tags,
        "choke_role": "",  # Agent / 人工补全
        "exclude_rule": None,
    }


def run_serenity_scan(
    theme: str,
    *,
    board_type: str = "concept",
    board_code: Optional[str] = None,
    top_boards: int = 3,
    max_candidates: int = MAX_CANDIDATES,
    attach_structure: bool = True,
    record_picks: bool = True,
    show_progress: bool = True,
) -> dict:
    """按主题匹配板块成份，打 Serenity 候选分并落盘。"""
    theme = str(theme or "").strip()
    if not theme and not board_code:
        return {"ok": False, "message": "请提供 --theme 板块名或 board_code", "candidates": []}

    board_type = board_type if board_type in ("concept", "industry") else "concept"
    _progress(f"Serenity 卡脖子扫描 · 主题={theme or board_code} · {board_type}", show_progress)

    boards = fetch_board_list(board_type, force_refresh=False, verbose=show_progress)
    if not boards:
        boards = fetch_board_list(board_type, force_refresh=True, verbose=show_progress)

    matched: List[dict] = []
    if board_code:
        code_u = str(board_code).strip().upper()
        matched = [b for b in boards if str(b.get("code") or "").upper() == code_u]
        if not matched:
            matched = [{
                "code": code_u,
                "name": theme or code_u,
                "pct_chg": 0,
                "board_score": 0,
                "match_mode": "explicit_code",
            }]
    else:
        matched = _match_boards(theme, boards, limit=max(1, top_boards))

    if not matched:
        return {
            "ok": False,
            "message": "未匹配到板块（接口空或主题过偏），请换主题或指定 board_code",
            "theme": theme,
            "candidates": [],
        }

    members: List[dict] = []
    seen: set[str] = set()
    for b in matched:
        bcode = str(b.get("code") or "")
        _progress(f"  成份 · {b.get('name')} ({bcode})", show_progress)
        for m in fetch_board_constituents(bcode, verbose=show_progress):
            c = str(m.get("code") or "").zfill(6)
            if not c or c in seen:
                continue
            seen.add(c)
            item = dict(m)
            item["board_code"] = bcode
            item["board_name"] = b.get("name")
            item["board_pct"] = b.get("pct_chg")
            members.append(item)

    _progress(f"  成份合计 {len(members)}，拉取行情…", show_progress)
    try:
        spot = get_market_spot(verbose=False)
    except Exception:
        spot = pd.DataFrame()
    spot_map = _enrich_spot([str(m.get("code")).zfill(6) for m in members], spot)

    structures: Dict[str, dict] = {}
    if attach_structure and members:
        codes = [str(m.get("code")).zfill(6) for m in members[:80]]

        def _one(code: str) -> Tuple[str, dict]:
            try:
                hist = get_stock_hist(code, days=120, patch_live=False)
                return code, _ma_structure(hist)
            except Exception:
                return code, {"label": "unknown", "rsi": None}

        with ThreadPoolExecutor(max_workers=HIST_WORKERS) as pool:
            futs = {pool.submit(_one, c): c for c in codes}
            for fut in as_completed(futs):
                code, st = fut.result()
                structures[code] = st

    candidates: List[dict] = []
    for m in members:
        code = str(m.get("code") or "").zfill(6)
        scored = _score_candidate(m, spot_map.get(code) or {}, structures.get(code) or {})
        if not scored:
            continue
        scored["board_code"] = m.get("board_code")
        scored["board_name"] = m.get("board_name")
        scored["board_pct"] = m.get("board_pct")
        candidates.append(scored)

    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = candidates[: max(1, max_candidates)]

    result = {
        "ok": True,
        "strategy_id": STRATEGY_ID,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "theme": theme,
        "board_type": board_type,
        "matched_boards": matched,
        "stats": {
            "board_count": len(matched),
            "member_count": len(members),
            "candidate_count": len(top),
            "pool_scored": len(candidates),
        },
        "candidates": top,
        "notes": [
            "定量分为流动性/市值弹性/涨幅克制/均线结构启发式；卡脖子定位需 Agent 按六步补全。",
            "本结果独立于 midterm / sector；勿混入 midterm_pick_tracker。",
        ],
    }
    result["markdown"] = format_serenity_markdown(result)
    path = _save_serenity_result(result)
    result["artifact"] = str(path)

    if record_picks and top:
        _append_pick_batch(theme, top)

    _print_table(result, show_progress)
    return result


def format_serenity_markdown(payload: dict) -> str:
    theme = payload.get("theme") or ""
    lines = [
        f"# Serenity 卡脖子扫描 · {theme}",
        "",
        f"- strategy: `{STRATEGY_ID}`",
        f"- 生成时间: {payload.get('generated_at')}",
        f"- 匹配板块数: {(payload.get('stats') or {}).get('board_count')}",
        f"- 候选数: {(payload.get('stats') or {}).get('candidate_count')}",
        "",
        "## 匹配板块",
        "",
    ]
    boards = payload.get("matched_boards") or []
    if boards:
        rows = [
            [
                b.get("code"),
                truncate_display(b.get("name"), 16),
                b.get("pct_chg"),
                b.get("match_mode") or "name_match",
            ]
            for b in boards
        ]
        lines.append(format_markdown_table(["代码", "名称", "涨跌%", "匹配"], rows))
    else:
        lines.append("（无）")

    lines.extend(["", "## 候选标的（待补 choke_role）", ""])
    cands = payload.get("candidates") or []
    if cands:
        rows = [
            [
                c.get("code"),
                truncate_display(c.get("name"), 10),
                c.get("score"),
                c.get("ma_structure"),
                c.get("pct_chg"),
                c.get("market_cap"),
                ",".join(c.get("tags") or [])[:20],
            ]
            for c in cands
        ]
        lines.append(
            format_markdown_table(
                ["代码", "名称", "分", "均线", "涨跌%", "市值亿", "标签"],
                rows,
            )
        )
    else:
        lines.append("（无候选）")

    lines.extend(
        [
            "",
            "## 下一步（Skill）",
            "1. 按六步补全周期/供应链地图/真伪排除",
            "2. 为保留标的填写 `choke_role`",
            "3. 需要买卖点时交 `stock-analysis-master` 二次过滤",
            "",
        ]
    )
    return "\n".join(lines)


def _save_serenity_result(result: dict) -> Path:
    SERENITY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SERENITY_OUTPUT_DIR / f"serenity_{stamp}.json"
    payload = sanitize_for_json(result)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = SERENITY_OUTPUT_DIR / "serenity_latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = SERENITY_OUTPUT_DIR / f"serenity_{stamp}.md"
    md_path.write_text(result.get("markdown") or "", encoding="utf-8")
    (SERENITY_OUTPUT_DIR / "serenity_latest.md").write_text(
        result.get("markdown") or "", encoding="utf-8"
    )
    return path


def load_latest_serenity() -> dict:
    path = SERENITY_OUTPUT_DIR / "serenity_latest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _append_pick_batch(theme: str, candidates: List[dict]) -> None:
    state = _load_picks()
    today = datetime.now().strftime("%Y-%m-%d")
    batch_id = f"{today}_{_normalize_theme(theme)[:20]}"
    records = []
    for c in candidates:
        rec = {
            "id": f"{batch_id}_{c.get('code')}",
            "batch_id": batch_id,
            "date": today,
            "theme": theme,
            "code": c.get("code"),
            "name": c.get("name"),
            "score": c.get("score"),
            "entry_price": c.get("price"),
            "ma_structure": c.get("ma_structure"),
            "board_name": c.get("board_name"),
            "status": "open",
            "strategy_id": STRATEGY_ID,
        }
        records.append(rec)
        state["records"].append(rec)
    state["batches"].append({
        "batch_id": batch_id,
        "date": today,
        "theme": theme,
        "count": len(records),
    })
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    _save_picks(state)


def run_serenity_track(*, show_progress: bool = True) -> dict:
    """独立跟进 Serenity 候选（不触碰 midterm_pick_tracker）。"""
    state = _load_picks()
    open_recs = [r for r in state.get("records") or [] if r.get("status") == "open"]
    _progress(f"Serenity 跟进 · 待评估 {len(open_recs)} 条", show_progress)
    evaluated = 0
    wins = 0
    returns: List[float] = []

    for rec in open_recs:
        code = str(rec.get("code") or "").zfill(6)
        entry = rec.get("entry_price")
        try:
            entry_f = float(entry) if entry is not None else None
        except (TypeError, ValueError):
            entry_f = None
        if not entry_f or entry_f <= 0:
            continue
        try:
            hist = get_stock_hist(code, days=40, patch_live=False)
        except Exception:
            continue
        if hist is None or hist.empty or "close" not in hist.columns:
            continue
        close = pd.to_numeric(hist["close"], errors="coerce").dropna()
        if close.empty:
            continue
        last = float(close.iloc[-1])
        ret = (last / entry_f - 1.0) * 100
        rec["last_price"] = round(last, 2)
        rec["return_pct"] = round(ret, 2)
        rec["evaluated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 简化：有足够 bar 且持有满跟进期则关闭（用记录日起估算交易日数）
        hold_days = int(rec.get("hold_bars") or 0) + 1
        rec["hold_bars"] = hold_days
        if hold_days >= FOLLOW_TRADING_DAYS:
            rec["status"] = "closed"
            rec["win"] = ret >= WIN_THRESHOLD_PCT
            evaluated += 1
            returns.append(ret)
            if rec["win"]:
                wins += 1

    closed = [r for r in state.get("records") or [] if r.get("status") == "closed"]
    n = len(closed)
    state["summary"] = {
        "closed_n": n,
        "win_rate": round(100.0 * sum(1 for r in closed if r.get("win")) / n, 1) if n else None,
        "avg_return": round(sum(float(r.get("return_pct") or 0) for r in closed) / n, 2) if n else None,
        "open_n": len([r for r in state.get("records") or [] if r.get("status") == "open"]),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_picks(state)
    _progress(
        f"  本轮推进 {evaluated} 笔到期；累计闭环 {n} · 胜率 {state['summary'].get('win_rate')}",
        show_progress,
    )
    return {
        "ok": True,
        "strategy_id": STRATEGY_ID,
        "summary": state["summary"],
        "advanced": evaluated,
    }


def _print_table(result: dict, show: bool) -> None:
    if not show:
        return
    print(result.get("markdown") or format_serenity_markdown(result))
