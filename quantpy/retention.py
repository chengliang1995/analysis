"""按日期命名的输出/日志文件保留策略（默认仅保留最近 3 天）。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from quantpy.paths import (
    LOG_DIR,
    MIDTERM_OUTPUT_DIR,
    OUTPUT_DIR,
    REAL_REVIEW_DIR,
    REPORT_DIR,
    RETENTION_DAYS,
    SIM_REVIEW_DIR,
)

_DATE8 = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_DATE10 = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


def _parse_date_from_name(name: str) -> Optional[date]:
    m = _DATE8.search(name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    m = _DATE10.search(name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def prune_dated_files(
    directory: Path,
    pattern: str,
    retention_days: int = RETENTION_DAYS,
) -> list[Path]:
    """删除目录中文件名含日期、且早于保留期的文件。"""
    if retention_days < 1 or not directory.exists():
        return []
    cutoff = date.today() - timedelta(days=retention_days)
    removed: list[Path] = []
    for path in directory.glob(pattern):
        if not path.is_file():
            continue
        file_date = _parse_date_from_name(path.name)
        if file_date is None or file_date > cutoff:
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def prune_retention_files(retention_days: int = RETENTION_DAYS) -> dict[str, list[str]]:
    """清理所有按日落盘的日志与报告，仅保留最近 retention_days 天。"""
    targets: Iterable[tuple[Path, str]] = (
        (LOG_DIR, "web_*.log"),
        (REPORT_DIR, "daily_report_*.md"),
        (REPORT_DIR, "daily_summary_*.json"),
        (OUTPUT_DIR, "ultra_short_*.csv"),
        (MIDTERM_OUTPUT_DIR, "midterm_*.json"),
        (MIDTERM_OUTPUT_DIR, "midterm_*.md"),
        (MIDTERM_OUTPUT_DIR, "level_alerts_*.json"),
        (REAL_REVIEW_DIR, "real_review_*.json"),
        (REAL_REVIEW_DIR, "real_review_*.md"),
        (SIM_REVIEW_DIR, "review_*.md"),
    )
    summary: dict[str, list[str]] = {}
    for directory, pattern in targets:
        removed = prune_dated_files(directory, pattern, retention_days)
        if removed:
            key = str(directory)
            summary[key] = [p.name for p in removed]
    return summary
