"""
test_features_metrics.py — unit tests for the web-layer metric math.

These functions render fine even when wrong (a bad Sharpe still returns HTTP 200),
so route tests can't catch them — this is where correctness is pinned. Pure
logic, no DB or network.
"""

import math

import web_common
from features.core import _series_metrics, _trade_metrics


# ── _series_metrics ─────────────────────────────────────────────────────────────

def test_series_metrics_basic_shape_and_drawdown():
    m = _series_metrics([100.0, -50.0, 100.0])
    # equity compounds off STARTING_CAPITAL
    base = web_common.STARTING_CAPITAL
    assert m["equity"] == [base + 100, base + 50, base + 150]
    assert m["drawdown"] == [0.0, -50.0, 0.0]
    assert m["max_dd"] == -50.0
    assert m["best_day"] == 100.0 and m["worst_day"] == -50.0
    # positive mean return -> positive Sharpe; one down day -> Sortino defined
    assert m["sharpe"] is not None and m["sharpe"] > 0
    assert m["sortino"] is not None
    assert m["vol_pct"] is not None and m["vol_pct"] > 0


def test_series_metrics_empty_is_none_safe():
    m = _series_metrics([])
    assert m["sharpe"] is None and m["sortino"] is None
    assert m["max_dd"] == 0.0 and m["equity"] == [] and m["drawdown"] == []


def test_series_metrics_all_up_has_no_drawdown():
    m = _series_metrics([10.0, 20.0, 30.0])
    assert m["max_dd"] == 0.0
    assert all(d == 0.0 for d in m["drawdown"])


# ── _trade_metrics ──────────────────────────────────────────────────────────────

def test_trade_metrics_mixed():
    m = _trade_metrics([10.0, -5.0, 20.0, -5.0])
    assert m["n"] == 4
    assert m["win_rate"] == 50.0
    assert m["profit_factor"] == 3.0 and m["pf_label"] == "3.00"
    assert m["avg_win"] == 15.0 and m["avg_loss"] == -5.0
    assert m["expectancy"] == 5.0
    assert m["largest_win"] == 20.0 and m["largest_loss"] == -5.0
    assert math.isclose(m["payoff"], 3.0)


def test_trade_metrics_all_wins_is_infinite_pf():
    m = _trade_metrics([5.0, 10.0])
    assert m["profit_factor"] is None          # undefined, not infinite float
    assert m["pf_label"] == "∞"
    assert m["win_rate"] == 100.0


def test_trade_metrics_empty_is_safe():
    m = _trade_metrics([])
    assert m["n"] == 0 and m["win_rate"] is None and m["pf_label"] == "—"


# ── sparkline_svg ───────────────────────────────────────────────────────────────

def test_sparkline_too_few_points_is_dash():
    assert "—" in web_common.sparkline_svg([])
    assert "—" in web_common.sparkline_svg([5])


def test_sparkline_renders_polyline():
    svg = web_common.sparkline_svg([1, 2, 3, 2, 5])
    assert "<svg" in svg and "<polyline" in svg


# ── live_price caching + fallback ────────────────────────────────────────────────

def test_live_price_caches_successful_fetch(monkeypatch):
    web_common._price_cache.clear()
    monkeypatch.setattr(web_common, "_fetch_last", lambda s: 99.5)
    assert web_common.live_price("FOO") == 99.5
    assert "FOO" in web_common._price_cache            # memoised


def test_live_price_falls_back_to_csv_close(monkeypatch):
    web_common._price_cache.clear()
    monkeypatch.setattr(web_common, "_fetch_last", lambda s: None)   # upstream empty
    monkeypatch.setattr(web_common, "last_close", lambda s: 42.0)
    assert web_common.live_price("BAR") == 42.0
