"""test_ceinsys_analysis.py — the CEINSYS swing harness runs and is self-consistent.

Uses a synthetic OHLC series (no network, no real data) purely to prove the
plumbing: horizon_study, price_action_backtest, live_plan and build_report all
run and return sane, bounded structures. It asserts NOTHING about CEINSYS's real
returns — those come only from data fetched on the user's machine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ceinsys_analysis as CA


def _synthetic(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    rets = rng.normal(0.0008, 0.025, n)          # small-cap-ish drift + vol
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    openp = close * (1 + rng.normal(0, 0.005, n))
    vol = rng.integers(50_000, 500_000, n)
    return pd.DataFrame(
        {"open": openp, "high": high, "low": low, "close": close, "volume": vol},
        index=pd.Index(dates, name="date"))


@pytest.mark.unit
def test_horizon_study_probabilities_are_valid():
    df = _synthetic()
    h = CA.horizon_study(df)
    assert h["n"] > 0
    for k in ("p_touch_target", "p_close_target", "p_down_20"):
        assert 0.0 <= h[k] <= 1.0
    # touching +20% intra-window must be at least as likely as ending +20%
    assert h["p_touch_target"] >= h["p_close_target"] - 1e-9


@pytest.mark.unit
def test_trend_filter_reduces_sample():
    df = _synthetic()
    all_n = CA.horizon_study(df)["n"]
    up = df["close"] > df["close"].rolling(CA.TREND_MA).mean()
    up_n = CA.horizon_study(df, up)["n"]
    assert 0 <= up_n <= all_n


@pytest.mark.unit
def test_live_plan_sizing_is_consistent():
    df = _synthetic()
    plan = CA.live_plan(df, capital=1_000_000)
    assert plan["stop"] < plan["entry"] < plan["target_price"]
    # risk to stop must not exceed the 1% budget (+1 share rounding tolerance)
    assert plan["capital_at_risk"] <= 1_000_000 * CA.RISK_PER_TRADE + plan["risk_per_share"]
    assert plan["qty"] >= 0


@pytest.mark.unit
def test_backtest_and_report_run():
    df = _synthetic()
    bt = CA.price_action_backtest(df)
    assert bt["n"] >= 0
    report = CA.build_report(df)
    assert "CEINSYS" in report and "5 months" in report
