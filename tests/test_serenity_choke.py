"""Serenity choke advisor — offline unit checks (no network)."""

from quantpy.serenity_choke_advisor import STRATEGY_ID, _match_boards, _score_candidate


def test_strategy_id_isolated():
    assert STRATEGY_ID == "serenity_choke"


def test_match_boards_prefers_name_hit():
    boards = [
        {"code": "BK1", "name": "固态电池", "pct_chg": 1, "board_score": 10},
        {"code": "BK2", "name": "白酒", "pct_chg": 5, "board_score": 99},
    ]
    hit = _match_boards("固态电池", boards, limit=2)
    assert hit[0]["code"] == "BK1"


def test_score_rejects_st():
    assert _score_candidate({"code": "000001", "name": "ST示例"}, {}, {}) is None


def test_score_prefers_small_cap():
    member = {"code": "688001", "name": "测试股", "pct_chg": 3, "turnover": 5}
    small = _score_candidate(member, {"pct_chg": 3, "turnover": 5, "market_cap": 50, "amount_yi": 3}, {"label": "多头"})
    large = _score_candidate(member, {"pct_chg": 3, "turnover": 5, "market_cap": 900, "amount_yi": 3}, {"label": "多头"})
    assert small is not None and large is not None
    assert small["score"] > large["score"]
