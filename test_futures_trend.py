"""
test_futures_trend.py — the futures TS-momentum signal (SPEC_futures_trend.md).

Pins the objective rules on deterministic synthetic series: direction follows the
trailing-year trend, size scales inversely with volatility, warmup is flat, and
there is no look-ahead (position at t uses only data through t). Pure logic.
"""
import numpy as np
import pandas as pd

import futures_trend as FT


def _series(closes, start="2020-01-01"):
    c = np.asarray(closes, float)
    idx = pd.bdate_range(start, periods=len(c))
    return pd.DataFrame({"close": c}, index=idx)


def test_trend_sign_long_in_uptrend_short_in_downtrend():
    up = _series(100 * np.cumprod(1 + np.full(400, 0.001)))
    dn = _series(100 * np.cumprod(1 + np.full(400, -0.001)))
    assert FT.trend_sign(up["close"]).iloc[-1] == 1.0
    assert FT.trend_sign(dn["close"]).iloc[-1] == -1.0


def test_vol_weight_inverse_to_volatility():
    rng = np.random.default_rng(0)
    calm = _series(100 * np.cumprod(1 + rng.normal(0, 0.004, 400)))
    wild = _series(100 * np.cumprod(1 + rng.normal(0, 0.02, 400)))
    wc = FT.vol_weight(calm["close"]).iloc[-1]
    ww = FT.vol_weight(wild["close"]).iloc[-1]
    assert wc > ww                                  # calmer market → larger weight


def test_vol_weight_capped():
    # extremely calm series would ask for a huge weight; must clip at WEIGHT_CAP
    s = _series(100 + np.linspace(0, 0.5, 400))       # near-zero vol drift
    assert FT.vol_weight(s["close"]).max() <= FT.WEIGHT_CAP + 1e-9


def test_warmup_is_flat():
    s = _series(100 * np.cumprod(1 + np.full(400, 0.001)))
    pos = FT.target_position(s)
    assert (pos.iloc[:FT.WARMUP] == 0.0).all()


def test_target_position_sign_matches_trend():
    up = _series(100 * np.cumprod(1 + np.full(400, 0.0012)))
    dn = _series(100 * np.cumprod(1 + np.full(400, -0.0012)))
    assert FT.target_position(up).iloc[-1] > 0
    assert FT.target_position(dn).iloc[-1] < 0


def test_no_lookahead_position_uses_only_past():
    # truncating the series at t must not change the position computed at t
    s = _series(100 * np.cumprod(1 + np.random.default_rng(1).normal(0.0005, 0.01, 400)))
    full = FT.target_position(s)
    t = 350
    truncated = FT.target_position(s.iloc[:t + 1])
    assert abs(full.iloc[t] - truncated.iloc[t]) < 1e-12


def test_zero_vol_is_safe():
    flat = _series(np.full(400, 100.0))               # zero returns → zero vol
    pos = FT.target_position(flat)
    assert np.isfinite(pos).all() and (pos.iloc[-1] == 0.0)
