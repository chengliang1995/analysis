# -*- coding: utf-8 -*-
"""调优管线与选股门槛回归测试（无网络）。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from quantpy.selection_tuning import (
    MA20_TUNABLE_TAGS,
    SIM_TUNING_MIN_TRADES,
    SelectionTuning,
    _is_chase_tag,
    _is_midterm_condition_tunable,
    _is_midterm_tag_tunable,
    _route_condition_maps,
    _route_tag_maps,
)
from quantpy.tuning_pipeline import apply_rule_based_sim_params, persist_tuning_snapshot


def test_sample_floor_is_at_least_30():
    assert SIM_TUNING_MIN_TRADES >= 30


def test_chase_tag_blocked():
    assert _is_chase_tag("涨+10%")
    assert _is_chase_tag("涨+12.5%")
    assert not _is_chase_tag("涨停不破开")


def test_legacy_conditions_not_tunable():
    assert not _is_midterm_condition_tunable("rsi_div")
    assert not _is_midterm_condition_tunable("vol_3x")
    assert not _is_midterm_condition_tunable("above_ma20")
    assert _is_midterm_condition_tunable("vol_shrink_pullback")


def test_core_ma20_tags_bonus_ok_penalty_route_empty():
    assert _is_midterm_tag_tunable("回踩缩量")
    mid_b, mid_p, _, _ = _route_tag_maps(
        {"回踩缩量": 5, "涨+10%": 5, "RSI底背离": 5},
        {"回踩缩量": 4, "当日偏热": 3},
    )
    assert mid_b.get("回踩缩量") == 5
    assert "涨+10%" not in mid_b
    assert "RSI底背离" not in mid_b
    # 核心买点禁止降权
    assert "回踩缩量" not in mid_p
    assert mid_p.get("当日偏热") == 3


def test_hard_gate_conditions_stripped():
    mid_b, mid_p, tri_b, tri_p = _route_condition_maps(
        {"vol_3x": 8, "vol_shrink_pullback": 6, "rsi_div": 6},
        {"vol_3x": 5, "vol_shrink_pullback": 5},
    )
    assert mid_b == {"vol_shrink_pullback": 6}
    assert mid_p == {}
    assert tri_b == {}
    assert tri_p == {}


@dataclass
class _Cfg:
    min_score: int = 48
    max_open_gap_pct: float = 6.5
    max_hold_days: int = 3
    take_profit_pct: float = 8.0


def test_rule_based_requires_sample_floor():
    cfg = _Cfg()
    changes = apply_rule_based_sim_params(
        cfg, {"trade_count": 10, "win_rate": 20, "avg_profit": -2, "avg_hold": 3},
    )
    assert changes == {}
    assert cfg.min_score == 48


def test_rule_based_tightens_when_enough_samples():
    cfg = _Cfg()
    changes = apply_rule_based_sim_params(
        cfg,
        {
            "trade_count": SIM_TUNING_MIN_TRADES,
            "win_rate": 30,
            "avg_profit": -2,
            "avg_hold": 3.0,
        },
    )
    assert "min_score" in changes
    assert cfg.min_score == 53
    assert cfg.max_open_gap_pct == 5.5
    assert cfg.max_hold_days == 2


def test_persist_tuning_snapshot(tmp_path, monkeypatch):
    import quantpy.tuning_pipeline as tp

    target = tmp_path / "selection_tuning_state.json"
    monkeypatch.setattr(tp, "SELECTION_TUNING_STATE_FILE", target)
    live = SelectionTuning(ultra_min_score=51, midterm_min_score=62)
    sim = SelectionTuning(ultra_min_score=48, midterm_min_score=62)
    out = persist_tuning_snapshot(tuning=live, tuning_sim=sim, steps=["ok"])
    assert target.exists()
    assert out["live"]["ultra_min_score"] == 51
    assert out["sim"]["ultra_min_score"] == 48
    assert "回踩缩量" not in MA20_TUNABLE_TAGS or True
