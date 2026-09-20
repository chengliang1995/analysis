"""midterm_level_alerts must tolerate missing quotes (current_price=None)."""

from quantpy.midterm_level_alerts import scan_midterm_level_alerts


def test_scan_alerts_tolerates_none_current_price(monkeypatch):
    # Avoid network / heavy review path
    monkeypatch.setattr(
        "quantpy.midterm_level_alerts.get_stock_recent_bars",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "quantpy.midterm_level_alerts.MidtermPortfolioAdvisor.review_holdings",
        lambda self, positions: [],
    )

    portfolio_stats = {
        "has_data": True,
        "positions": [
            {
                "code": "600519",
                "name": "贵州茅台",
                "quantity": 100,
                "cost_price": 1600.0,
                "current_price": None,  # missing quote
                "bucket": "midterm",
            }
        ],
    }
    out = scan_midterm_level_alerts(portfolio_stats, reviews=[], save=False)
    assert isinstance(out, dict)
    assert out.get("alerts") == [] or isinstance(out.get("alerts"), list)
