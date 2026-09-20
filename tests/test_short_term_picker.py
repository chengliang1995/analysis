"""short_term_picker — offline unit checks."""

import pandas as pd

from quantpy.short_term_picker import (
    STRATEGY_ID,
    _allowed_board,
    _analyze_one,
    _board_label,
    _cap_to_yi,
    format_short_term_markdown,
)


def test_strategy_id():
    assert STRATEGY_ID == "short_term_picker"


def test_board_filters():
    assert _allowed_board("600519")
    assert _allowed_board("300001")
    assert _allowed_board("688001")
    assert not _allowed_board("920001")  # 北交所风格
    assert _board_label("300001") == "创业板"
    assert _board_label("688001") == "科创板"


def test_cap_to_yi():
    assert abs(_cap_to_yi(15_000_000_000) - 150.0) < 1e-6
    assert abs(_cap_to_yi(80.5) - 80.5) < 1e-6
    assert _cap_to_yi(None) is None


def test_analyze_rejects_st():
    assert _analyze_one("600000", "ST示例", limit_count=2, market_cap_raw=50e8) is None


def test_analyze_rejects_over_cap():
    assert _analyze_one("600000", "测试", limit_count=2, market_cap_raw=200e8) is None


def test_markdown_empty():
    md = format_short_term_markdown({"stats": {}, "candidates": [], "generated_at": "t", "source": "x"})
    assert "short_term_picker" in md
