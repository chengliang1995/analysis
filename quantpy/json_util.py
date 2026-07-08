"""JSON 序列化辅助：将 NaN/Inf 转为 null，避免前端解析失败。"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd


def json_safe_float(value: Any, *, digits: Optional[int] = None) -> Optional[float]:
    """将数值转为 JSON 安全浮点；无效值返回 None。"""
    if value is None or value == "":
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return round(num, digits) if digits is not None else num


def sanitize_for_json(obj: Any) -> Any:
    """递归清理 dict/list，使标准 JSON 可解析（NaN/Inf → null）。"""
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        val = float(obj)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if obj is pd.NA:
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


def df_to_records_safe(df: pd.DataFrame) -> list[dict]:
    """DataFrame → records，NaN 转为 None。"""
    if df is None or df.empty:
        return []
    cleaned = df.where(pd.notnull(df), None)
    return sanitize_for_json(cleaned.to_dict("records"))
