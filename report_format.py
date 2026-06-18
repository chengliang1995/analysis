"""Markdown 表格对齐（兼容中文双宽字符）。"""

from __future__ import annotations

import unicodedata
from typing import Iterable, List, Sequence


def display_width(text: object) -> int:
    width = 0
    for ch in str(text):
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            width += 2
        else:
            width += 1
    return width


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
    str_rows: List[List[str]] = [[str(cell) for cell in row] for row in rows]
    if aligns is None:
        aligns = ["left"] * len(headers)

    all_rows = [list(headers)] + str_rows
    col_widths = [
        max(display_width(row[i]) for row in all_rows)
        for i in range(len(headers))
    ]

    def _format_row(cells: Sequence[str]) -> str:
        parts = [
            f" {pad_cell(cells[i], col_widths[i], aligns[i] if i < len(aligns) else 'left')} "
            for i in range(len(headers))
        ]
        return "|" + "|".join(parts) + "|"

    lines = [_format_row(headers)]
    lines.append("|" + "|".join("-" * (w + 2) for w in col_widths) + "|")
    lines.extend(_format_row(row) for row in str_rows)
    return "\n".join(lines) + "\n"
