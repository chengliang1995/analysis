"""Markdown 表格对齐（兼容中文双宽字符）。"""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Sequence


def display_width(text: object) -> int:
    """计算终端显示宽度；中文等宽字符按 2 计。"""
    width = 0
    for ch in str(text):
        if unicodedata.east_asian_width(ch) in ("F", "W", "A"):
            width += 2
        else:
            width += 1
    return width


def truncate_display(text: object, max_width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断文本。"""
    s = str(text)
    if display_width(s) <= max_width:
        return s
    ell_w = display_width(ellipsis)
    limit = max(0, max_width - ell_w)
    out: List[str] = []
    used = 0
    for ch in s:
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1
        if used + ch_w > limit:
            break
        out.append(ch)
        used += ch_w
    return "".join(out) + ellipsis


def pad_cell(text: object, width: int, align: str = "left") -> str:
    text = str(text)
    padding = max(0, width - display_width(text))
    if align == "right":
        return " " * padding + text
    if align == "center":
        left = padding // 2
        return " " * left + text + " " * (padding - left)
    return text + " " * padding


def format_markdown_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    aligns: Sequence[str] | None = None,
) -> str:
    """生成列对齐的 Markdown 表格（终端 / 日志按显示宽度对齐）。"""
    str_rows: List[List[str]] = [[str(cell) for cell in row] for row in rows]
    if aligns is None:
        aligns = ["left"] * len(headers)

    all_rows = [list(headers)] + str_rows
    col_widths = [
        max(display_width(row[i]) for row in all_rows)
        for i in range(len(headers))
    ]

    def _format_row(cells: Sequence[str]) -> tuple[str, List[str]]:
        parts: List[str] = []
        for col_idx in range(len(headers)):
            align = aligns[col_idx] if col_idx < len(aligns) else "left"
            inner = pad_cell(cells[col_idx], col_widths[col_idx], align)
            parts.append(f" {inner} ")
        return "|" + "|".join(parts) + "|", parts

    header_line, header_parts = _format_row(headers)
    lines = [header_line]
    lines.append(
        "|" + "|".join("-" * display_width(part) for part in header_parts) + "|"
    )
    for row in str_rows:
        line, _ = _format_row(row)
        lines.append(line)
    return "\n".join(lines) + "\n"


def format_plain_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    aligns: Sequence[str] | None = None,
) -> str:
    """纯文本表格（与 Markdown 表格相同对齐规则，便于日志输出）。"""
    return format_markdown_table(headers, rows, aligns=aligns)
