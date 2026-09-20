"""Serenity sim account — offline checks."""

from quantpy.serenity_choke_advisor import STRATEGY_ID
from quantpy.sim_serenity import (
    DEFAULT_SERENITY_CAPITAL,
    default_serenity_state,
    ensure_serenity_state,
    enrich_serenity_sim,
)


def test_default_capital_20w():
    st = default_serenity_state()
    assert st["initial_capital"] == DEFAULT_SERENITY_CAPITAL == 200_000.0
    assert st["cash"] == 200_000.0
    assert st["strategy"] == STRATEGY_ID


def test_ensure_creates_isolated_bucket():
    state: dict = {}
    mt = ensure_serenity_state(state)
    assert "serenity" in state
    assert mt is state["serenity"]
    assert "midterm" not in state
    assert "midterm_ma20" not in state


def test_enrich_empty_account():
    summary = enrich_serenity_sim({})
    assert summary["initial_capital"] == 200_000.0
    assert summary["position_count"] == 0
    assert summary["strategy"] == STRATEGY_ID
