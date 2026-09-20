"""short_term sim account — offline checks."""

from quantpy.short_term_picker import STRATEGY_ID
from quantpy.sim_short_term import (
    DEFAULT_SHORT_TERM_CAPITAL,
    default_short_term_state,
    ensure_short_term_state,
    enrich_short_term_sim,
)


def test_default_capital_20w():
    st = default_short_term_state()
    assert st["initial_capital"] == DEFAULT_SHORT_TERM_CAPITAL == 200_000.0
    assert st["strategy"] == STRATEGY_ID


def test_ensure_isolated():
    state: dict = {}
    mt = ensure_short_term_state(state)
    assert "short_term" in state
    assert mt is state["short_term"]
    assert "serenity" not in state


def test_enrich_empty():
    summary = enrich_short_term_sim({})
    assert summary["initial_capital"] == 200_000.0
    assert summary["position_count"] == 0
