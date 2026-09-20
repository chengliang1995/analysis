"""Tests for evidence-based strategy policy (no invented samples)."""

from quantpy.selection_tuning import SelectionTuning
from quantpy.strategy_policy import StrategyPolicy, apply_policy_to_tuning, build_policy_from_eval


def _fake_eval() -> dict:
    return {
        "fee_drag_pct": 0.1,
        "strategies": [
            {
                "id": "tracker_triple_breakout",
                "n": 27,
                "win_rate": 63.0,
                "avg_return": 5.78,
                "verdict": "主策略候选",
            },
            {
                "id": "tv_watchlist_shrink",
                "n": 109,
                "win_rate": 14.7,
                "avg_return": -1.96,
                "verdict": "不适用/应降级",
            },
            {
                "id": "sim_ultra_short",
                "n": 121,
                "win_rate": 34.7,
                "avg_return": 0.64,
                "verdict": "不适用/应降级",
            },
            {
                "id": "tracker_ma20_pullback",
                "n": 8,
                "win_rate": 25.0,
                "avg_return": -4.18,
                "verdict": "不适用/应降级",
            },
        ],
    }


def test_policy_prefers_breakout_and_demotes_watchlist():
    policy = build_policy_from_eval(_fake_eval())
    assert policy.prefer_breakout_day is True
    assert policy.midterm_entry_mode == "breakout_day"
    assert policy.demote_watchlist_as_primary is True
    assert policy.ultra_force_seal_filter is True
    assert policy.ultra_min_score_floor == 51
    assert any("突破日" in a for a in policy.actions)
    assert any("观察池" in a for a in policy.actions)


def test_policy_does_not_fire_on_thin_sample():
    thin = {
        "strategies": [
            {"id": "sim_ultra_short", "n": 5, "win_rate": 10.0, "avg_return": -5.0},
        ]
    }
    policy = build_policy_from_eval(thin)
    assert policy.ultra_force_seal_filter is False
    assert policy.ultra_min_score_floor is None


def test_apply_policy_raises_floors_only():
    tuning = SelectionTuning(ultra_min_score=40, triple_min_score=60, ma20_pullback_min_score=62)
    policy = StrategyPolicy(
        ultra_force_seal_filter=True,
        ultra_min_score_floor=51,
        triple_min_score_floor=68,
        ma20_min_score_floor=68,
        actions=["test action"],
        midterm_entry_mode="breakout_day",
    )
    applied = apply_policy_to_tuning(tuning, policy)
    assert tuning.ultra_min_score == 51
    assert tuning.triple_min_score == 68
    assert tuning.ma20_pullback_min_score == 68
    assert tuning.strict_tag_filter is True
    assert tuning.midterm_entry_mode == "breakout_day"
    assert "test action" in tuning.notes
    assert applied
