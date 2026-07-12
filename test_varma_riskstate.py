"""
test_varma_riskstate.py — pins the graded risk-state sizer (SPEC_varma_riskstate).

Guards the design invariants so the overlay stays honest: bounded in
[FLOOR, 1.0], monotone non-increasing in the risk state, never more aggressive
than the live binary overlay in its stress state (strict generalization), and
fail-safe on bad input. Pure logic on synthetic/real closes — no I/O, no orders.
"""
import numpy as np
import pandas as pd

import varma_riskstate as V
import regime_overlay as RO
from regime import BULL, BEAR, SIDEWAYS


# ── risk_score: blending + renormalization ────────────────────────────────────

def test_risk_score_bounds_and_extremes():
    lo, _ = V.risk_score(BULL, 0.0)          # calm bull, low vol
    hi, _ = V.risk_score(BEAR, 1.0)          # bear, top vol
    assert lo == 0.0
    assert hi == 1.0
    mid, _ = V.risk_score(SIDEWAYS, 0.5)
    assert 0.0 < mid < 1.0


def test_risk_score_monotone_in_vol():
    prev = -1.0
    for vp in (0.0, 0.25, 0.5, 0.75, 1.0):
        s, _ = V.risk_score(SIDEWAYS, vp)
        assert s >= prev
        prev = s


def test_breadth_absent_renormalizes():
    # Without breadth the trend+vol weights must renormalize to sum 1, so a pure
    # bear + top-vol read is still exactly 1.0 (not W_TREND+W_VOL = 0.9).
    s, comps = V.risk_score(BEAR, 1.0, breadth_label=None)
    assert s == 1.0
    assert comps["breadth_risk"] is None


def test_breadth_narrow_adds_risk():
    base, _ = V.risk_score(BULL, 0.2, breadth_label=None)
    narrow, _ = V.risk_score(BULL, 0.2, breadth_label="narrow")
    assert narrow >= base


def test_risk_score_none_on_missing_axis():
    s, _ = V.risk_score(None, 0.5)
    assert s is None
    s2, _ = V.risk_score(BULL, None)
    assert s2 is None


# ── exposure_factor: bounds, monotonicity, fail-safe ──────────────────────────

def _series(closes):
    idx = pd.bdate_range("2019-01-01", periods=len(closes))
    return pd.Series(np.asarray(closes, float), index=idx)


def test_factor_never_exceeds_one_and_respects_floor():
    rng = np.random.default_rng(0)
    for seed_scale in (0.008, 0.02, 0.05):
        closes = 100 * np.cumprod(1 + rng.normal(0.0002, seed_scale, 600))
        r = V.exposure_factor(_series(closes))
        assert V.FLOOR - 1e-9 <= r["factor"] <= 1.0


def test_factor_monotone_non_increasing_in_risk():
    # Feed synthetic risk states straight through the mapping.
    prev = 2.0
    for score in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        f = V._snap(V._factor_from_score(score))
        assert f <= prev + 1e-9
        prev = f
    assert V._snap(V._factor_from_score(0.0)) == 1.0
    assert V._snap(V._factor_from_score(1.0)) == V.FLOOR


def test_calm_bull_sizes_well_above_floor_and_no_stress():
    # Steady uptrend -> bull trend -> not stressed and sized clearly above FLOOR.
    # (Absolute exposure near 1.0 is covered by the mapping test + real-data demo;
    # vol_percentile is relative to its own year, so a synthetic series can't pin
    # it exactly — assert only the controllable property here.)
    closes = 100 * np.cumprod(1 + np.full(500, 0.0007) +
                              np.random.default_rng(1).normal(0, 0.004, 500))
    r = V.exposure_factor(_series(closes))
    assert not r["stress"]
    assert r["factor"] > V.FLOOR
    assert r["regime"]["trend"] == BULL


def test_failsafe_on_short_history():
    r = V.exposure_factor(_series([100, 101, 102, 103]))
    assert r["factor"] == V.NEUTRAL_FACTOR
    assert r["risk_score"] is None


def test_never_raises_on_junk():
    for junk in (pd.Series(dtype=float), _series([np.nan] * 300)):
        r = V.exposure_factor(junk)
        assert r["factor"] <= 1.0            # degrades, does not throw


# ── strict generalization vs the live binary overlay ──────────────────────────

def test_strict_generalization_in_stress_state():
    """Wherever the live overlay fires its 0.50 stress factor, this graded sizer
    must be <= 0.50 — never more aggressive than the incumbent in that state."""
    df = pd.read_csv("data/NIFTY50.csv")
    s = pd.Series(df["close"].values, index=pd.to_datetime(df["date"])).sort_index()
    checked = 0
    for d in s.resample("ME").last().dropna().index:
        win = s[s.index <= d]
        base = RO.exposure_factor(win)
        if base["stress"]:                   # incumbent in stress
            grad = V.exposure_factor(win)
            assert grad["stress"]
            assert grad["factor"] <= RO.STRESS_EXPOSURE + 1e-9
            checked += 1
    # If history never hit the stress state, assert the property synthetically.
    if checked == 0:
        # bear + top-vol read must cap <= 0.50 by construction
        assert V._snap(min(V._factor_from_score(1.0), V.STRESS_CAP)) <= 0.50


def test_matches_live_trigger_on_real_series():
    """On the real NIFTY series the tail brake must engage on exactly the same
    (bear AND vol>=85th pctl) condition the incumbent uses."""
    df = pd.read_csv("data/NIFTY50.csv")
    s = pd.Series(df["close"].values, index=pd.to_datetime(df["date"])).sort_index()
    r = V.exposure_factor(s)
    m = r["regime"]["measures"]
    expect_stress = (r["regime"]["trend"] == BEAR
                     and m["vol_percentile_1y"] >= V.EXTREME_VOL_PCTL)
    assert r["stress"] == expect_stress
    assert r["factor"] <= 1.0
