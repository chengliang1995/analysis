"""板块股票推荐：热门概念/行业板块 + 成份股优选。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from quantpy.paths import OUTPUT_DIR
from quantpy.report_format import format_markdown_table, truncate_display
from quantpy.stock_data import fetch_board_constituents, fetch_board_list

BOARD_TYPE_LABELS = {
    "concept": "概念板块",
    "industry": "行业板块",
}

SECTOR_OUTPUT_DIR = OUTPUT_DIR / "sector"


def _progress(msg: str, show: bool) -> None:
    if show:
        print(msg, flush=True)


def _score_member(member: dict, leader_code: str) -> Optional[dict]:
    name = str(member.get("name") or "")
    if "ST" in name.upper() or "退" in name:
        return None
    code = str(member.get("code") or "").zfill(6)
    pct = float(member.get("pct_chg") or 0)
    turnover = float(member.get("turnover") or 0)
    if pct <= -9.5:
        return None

    tags: List[str] = []
    score = pct * 2.5 + min(max(turnover, 0), 15) * 0.75
    if code == str(leader_code or "").zfill(6):
        score += 8
        tags.append("领涨")
    if pct >= 9.5:
        score += 6
        tags.append("涨停")
    elif pct >= 5:
        tags.append("强势")
    if turnover >= 8:
        tags.append("高换手")

    out = dict(member)
    out["score"] = round(score, 1)
    out["tags"] = tags
    return out


def run_sector_recommendations(
    board_type: str = "concept",
    board_code: Optional[str] = None,
    top_boards: int = 8,
    stocks_per_board: int = 5,
    show_progress: bool = True,
) -> dict:
    """扫描热门板块并推荐成份股。可指定单个板块代码（如 BK0901）。"""
    board_type = board_type if board_type in BOARD_TYPE_LABELS else "concept"
    label = BOARD_TYPE_LABELS[board_type]
    board_code = (board_code or "").strip().upper() or None

    _progress(f"板块推荐 · {label}", show_progress)
    _progress("  拉取板块列表…", show_progress)
    boards = fetch_board_list(board_type)
    if not boards:
        return {
            "ok": False,
            "message": "板块列表为空，请稍后重试",
            "board_type": board_type,
            "board_type_label": label,
            "hot_boards": [],
            "recommendations": [],
            "stats": {},
        }

    if board_code:
        boards = [b for b in boards if b.get("code") == board_code]
        if not boards:
            boards = [{
                "code": board_code,
                "name": board_code,
                "pct_chg": 0,
                "turnover": 0,
                "up_count": 0,
                "down_count": 0,
                "leader_name": "",
                "leader_code": "",
                "leader_pct": 0,
                "board_score": 0,
            }]
        target_boards = boards
    else:
        target_boards = boards[: max(1, top_boards)]

    _progress(f"  分析 {len(target_boards)} 个板块成份…", show_progress)
    recommendations: List[dict] = []
    seen_codes: set[str] = set()

    for i, board in enumerate(target_boards, 1):
        bcode = board.get("code") or ""
        bname = board.get("name") or bcode
        _progress(f"  [{i}/{len(target_boards)}] {bname} ({bcode})", show_progress)
        members = fetch_board_constituents(bcode)
        scored: List[dict] = []
        for m in members:
            item = _score_member(m, str(board.get("leader_code") or ""))
            if not item:
                continue
            item["board_code"] = bcode
            item["board_name"] = bname
            item["board_type"] = board_type
            item["board_pct"] = board.get("pct_chg", 0)
            scored.append(item)
        scored.sort(key=lambda x: x.get("score", 0), reverse=True)

        added = 0
        for item in scored:
            code = str(item.get("code") or "").zfill(6)
            if code in seen_codes:
                continue
            seen_codes.add(code)
            recommendations.append(item)
            added += 1
            if added >= stocks_per_board:
                break

    recommendations.sort(key=lambda x: (x.get("board_pct", 0), x.get("score", 0)), reverse=True)

    result = {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "board_type": board_type,
        "board_type_label": label,
        "board_code": board_code,
        "hot_boards": target_boards,
        "recommendations": recommendations,
        "stats": {
            "board_count": len(target_boards),
            "stock_count": len(recommendations),
            "total_boards": len(boards),
        },
        "markdown": format_sector_report_markdown({
            "board_type_label": label,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hot_boards": target_boards,
            "recommendations": recommendations,
            "stats": {
                "board_count": len(target_boards),
                "stock_count": len(recommendations),
            },
        }),
    }
    _save_sector_result(result)
    _print_sector_tables(result, show_progress)
    return result


def format_sector_report_markdown(payload: dict) -> str:
    label = payload.get("board_type_label") or "板块"
    ts = payload.get("generated_at") or ""
    stats = payload.get("stats") or {}
    parts = [
        f"## {label}股票推荐\n",
        f"*生成时间: {ts} · 板块 {stats.get('board_count', 0)} 个 · 推荐 {stats.get('stock_count', 0)} 只*\n\n",
    ]

    hot = payload.get("hot_boards") or []
    if hot:
        parts.append("### 热门板块\n\n")
        board_rows = [
            [
                i,
                b.get("name", ""),
                b.get("code", ""),
                f"{b.get('pct_chg', 0):.2f}",
                b.get("up_count", 0),
                b.get("down_count", 0),
                truncate_display(b.get("leader_name", ""), 8),
                f"{b.get('leader_pct', 0):.2f}",
            ]
            for i, b in enumerate(hot, 1)
        ]
        parts.append(format_markdown_table(
            ["排名", "板块", "代码", "涨幅%", "上涨", "下跌", "领涨股", "领涨%"],
            board_rows,
            aligns=["right", "left", "left", "right", "right", "right", "left", "right"],
        ))

    recs = payload.get("recommendations") or []
    if recs:
        parts.append("\n### 成份股推荐\n\n")
        stock_rows = [
            [
                r.get("board_name", ""),
                r.get("code", ""),
                r.get("name", ""),
                f"{r.get('price', 0):.2f}",
                f"{r.get('pct_chg', 0):.2f}",
                f"{r.get('turnover', 0):.2f}",
                r.get("score", 0),
                truncate_display(",".join(r.get("tags") or []), 16),
            ]
            for r in recs
        ]
        parts.append(format_markdown_table(
            ["板块", "代码", "名称", "现价", "涨幅%", "换手%", "评分", "标签"],
            stock_rows,
            aligns=["left", "left", "left", "right", "right", "right", "right", "left"],
        ))
    else:
        parts.append("\n暂无推荐标的。\n")
    return "".join(parts)


def _save_sector_result(result: dict) -> Path:
    SECTOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SECTOR_OUTPUT_DIR / f"sector_{stamp}.json"
    slim = {k: v for k, v in result.items() if k != "markdown"}
    path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = SECTOR_OUTPUT_DIR / "sector_latest.json"
    latest.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_latest_sector() -> dict:
    path = SECTOR_OUTPUT_DIR / "sector_latest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _print_sector_tables(result: dict, show: bool) -> None:
    if not show:
        return
    md = result.get("markdown") or format_sector_report_markdown(result)
    if md.strip():
        print(md, end="" if md.endswith("\n") else "\n")
