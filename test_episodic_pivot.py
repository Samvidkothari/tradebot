"""
test_episodic_pivot.py — pins the mechanical EP ignition + sell-into-strength
exit (SPEC_episodic_pivot.md).

Guards the objective rules so the sleeve stays honest: ignition needs all three
conditions (volume surge + thrust + fresh high), entries never look ahead (entry
is the bar AFTER the ignition), trade geometry is valid, and degenerate input is
handled. Pure logic on synthetic OHLCV — no I/O, no orders.
"""
import numpy as np
import pandas as pd

import episodic_pivot as EP


def _ohlcv(closes, volumes=None, idx0="2021-01-01"):
    c = np.asarray(closes, float)
    n = len(c)
    v = (np.full(n, 1_000_000.0) if volumes is None
         else np.asarray(volumes, float))
    idx = pd.bdate_range(idx0, periods=n)
    # opens = prior close (gap-free baseline); small symmetric wicks
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002,
                         "low": np.minimum(o, c) * 0.998, "close": c,
                         "volume": v}, index=idx)


def _quiet_uptrend(n=140, start=100.0, drift=0.001, seed=0):
    rng = np.random.default_rng(seed)
    return start * np.cumprod(1 + drift + rng.normal(0, 0.004, n))


def test_atr_positive_and_length():
    df = _ohlcv(_quiet_uptrend(90))
    a = EP.atr(df)
    assert len(a) == len(df) and np.all(a[EP.ATR_N:] > 0)


def test_ignition_requires_all_three_conditions():
    # Build a calm base, then one engineered ignition bar: +8% thrust, huge volume,
    # new high. It must fire; the calm bars before it must not.
    base = list(np.linspace(100, 105, 120))          # gentle rise, no thrust
    closes = base + [base[-1] * 1.08]                # +8% thrust bar to a new high
    vols = [1_000_000.0] * 120 + [5_000_000.0]       # 5× volume on the ignition
    df = _ohlcv(closes, vols)
    mask = EP.ignition_mask(df)
    assert mask[-1], "engineered ignition (vol+thrust+new-high) should fire"
    assert not mask[:-1].any(), "calm base bars must not fire"


def test_no_ignition_without_volume():
    # Same +8% thrust to a new high but NORMAL volume -> not an EP.
    base = list(np.linspace(100, 105, 120))
    closes = base + [base[-1] * 1.08]
    vols = [1_000_000.0] * 121                        # no surge
    df = _ohlcv(closes, vols)
    assert not EP.ignition_mask(df).any()


def test_no_ignition_without_thrust():
    # Huge volume, new high, but only +1% move -> below THRUST_MIN.
    base = list(np.linspace(100, 105, 120))
    closes = base + [base[-1] * 1.01]
    vols = [1_000_000.0] * 120 + [9_000_000.0]
    df = _ohlcv(closes, vols)
    assert not EP.ignition_mask(df).any()


def test_entry_is_bar_after_ignition_no_lookahead():
    base = list(np.linspace(100, 105, 120))
    closes = base + [base[-1] * 1.08] + list(base[-1] * 1.08 * np.array([1.01, 1.02, 1.03]))
    vols = [1_000_000.0] * 120 + [6_000_000.0] + [1_500_000.0] * 3
    df = _ohlcv(closes, vols)
    trades = EP.generate_trades(df)
    assert trades, "should produce at least one trade"
    for t in trades:
        assert t["ignite_date"] < t["entry_date"]        # enter AFTER the signal
        assert t["entry_date"] <= t["exit_date"]
        assert t["side"] == "long"
        assert t["risk"] > 0
        assert t["sl"] < t["entry"]                       # long geometry


def test_generate_trades_shape_on_random_series():
    rng = np.random.default_rng(3)
    n = 500
    c = 100 * np.cumprod(1 + rng.normal(0.0004, 0.02, n))
    v = rng.lognormal(mean=13, sigma=0.6, size=n)
    df = _ohlcv(c, v)
    trades = EP.generate_trades(df)
    assert isinstance(trades, list)
    for t in trades:
        assert t["entry_date"] <= t["exit_date"]
        assert t["risk"] > 0
        assert t["sl"] < t["entry"]
        assert np.isfinite(t["gross_ret"])


def test_too_short_series_no_trades():
    assert EP.generate_trades(_ohlcv([100, 101, 102, 103, 104])) == []


def test_positions_do_not_overlap():
    # After a trade opens, no new entry until it closes (one position at a time).
    rng = np.random.default_rng(7)
    c = 100 * np.cumprod(1 + rng.normal(0.001, 0.025, 600))
    v = rng.lognormal(mean=13, sigma=0.7, size=600)
    df = _ohlcv(c, v)
    trades = EP.generate_trades(df)
    for a, b in zip(trades, trades[1:]):
        assert a["exit_date"] < b["entry_date"], "trades must not overlap"
