# -*- coding: utf-8 -*-
"""patch_live 默认与 orchestration 分发回归（无网络）。"""

from __future__ import annotations

import inspect

import pytest

from quantpy.orchestration import (
    CLI_ACTION_MAP,
    WEB_ACTION_ALIASES,
    dispatch_action,
)
from quantpy.stock_data import get_stock_hist


def test_get_stock_hist_patch_live_defaults_false():
    sig = inspect.signature(get_stock_hist)
    assert sig.parameters["patch_live"].default is False


def test_cli_action_map_covers_web_actions():
    expected = {
        "scan",
        "report",
        "midterm",
        "midterm-track",
        "midterm-triple-volume",
        "triple-volume-watch",
        "sim",
        "sim-review",
        "sim-backtest",
        "sim-midterm",
        "sim-midterm-select",
        "sim-ma20",
        "review",
        "review-tune",
        "ai-learn",
        "sector",
        "alerts",
        "refresh",
    }
    assert expected.issubset(set(CLI_ACTION_MAP.keys()))


def test_web_alias_sim_ma20_select():
    assert WEB_ACTION_ALIASES.get("sim-ma20-select") == "sim-ma20"


def test_dispatch_unknown_action():
    out = dispatch_action("not-a-real-action")
    assert out["ok"] is False
    assert "未知操作" in out["message"]


def test_dispatch_rejects_unexpected_kwargs():
    with pytest.raises(TypeError):
        dispatch_action("refresh", bogus=True)


def test_web_api_action_routes_via_dispatch(monkeypatch):
    from quantpy import web_app

    calls = []

    def _fake_dispatch(action: str, **kwargs):
        calls.append((action, kwargs))
        return {
            "ok": True,
            "message": "ok",
            "payload": {"ultra_short": [{"code": "000001"}]},
            "artifacts": [],
        }

    monkeypatch.setattr("quantpy.orchestration.dispatch_action", _fake_dispatch)
    monkeypatch.setattr(web_app, "get_dashboard_data", lambda **_k: {"stub": True})

    client = web_app.app.test_client()
    resp = client.post("/api/actions/scan")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["ultra_short"] == [{"code": "000001"}]
    assert calls and calls[0][0] == "scan"


def test_web_unknown_action_is_400(monkeypatch):
    from quantpy import web_app

    client = web_app.app.test_client()
    resp = client.post("/api/actions/not-a-real-action")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "未知操作" in body["message"]
