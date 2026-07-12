"""
test_trailing_exit.py — the shared ratcheting trailing-stop engine (Pillar 4).

Pins the core invariant (the stop never moves backward), no look-ahead, breakeven
locking, and correct exit detection for both sides. Pure logic on synthetic bars.
"""
import numpy as np
import pandas as pd

import trailing_exit as TE
from trailing_exit import TrailConfig


def _bars(closes, idx0="2022-01-01"):
    c = np.asarray(closes, float)
    idx = pd.bdate_range(idx0, periods=len(c))
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003,
                         "low": np.minimum(o, c) * 0.997, "close": c}, index=idx)


def test_long_stop_never_moves_backward():
    # choppy uptrend: the ratcheted stop must be monotone non-decreasing
    rng = np.random.default_rng(0)
    c = 100 * np.cumprod(1 + rng.normal(0.0015, 0.01, 200))
    df = _bars(c)
    path = TE.stop_path(df, 0, entry=df["close"].iloc[0],
                        init_stop=df["close"].iloc[0] * 0.95, side="long")
    p = path[~np.isnan(path)]
    assert np.all(np.diff(p) >= -1e-9), "long stop moved backward"


def test_short_stop_never_moves_backward():
    rng = np.random.default_rng(1)
    c = 100 * np.cumprod(1 + rng.normal(-0.0015, 0.01, 200))
    df = _bars(c)
    path = TE.stop_path(df, 0, entry=df["close"].iloc[0],
                        init_stop=df["close"].iloc[0] * 1.05, side="short")
    p = path[~np.isnan(path)]
    assert np.all(np.diff(p) <= 1e-9), "short stop moved backward"


def test_stop_stays_below_price_for_long():
    c = 100 * np.cumprod(1 + np.full(120, 0.004))
    df = _bars(c)
    path = TE.stop_path(df, 0, df["close"].iloc[0], df["close"].iloc[0] * 0.95, "long")
    for i in range(len(df)):
        if not np.isnan(path[i]):
            assert path[i] <= df["close"].iloc[i] + 1e-9


def test_breakeven_locks_after_R():
    # steady rally: once +1R is reached the stop must never fall below entry
    entry = 100.0
    c = 100 * np.cumprod(1 + np.full(80, 0.01))
    df = _bars(c)
    init_stop = 95.0                                   # 1R = 5 points
    cfg = TrailConfig(n_bar=10, atr_mult=5.0, breakeven_R=1.0)
    path = TE.stop_path(df, 0, entry, init_stop, "long", cfg)
    # find first bar reaching +1R on the high
    reached = np.argmax(df["high"].values >= entry + 5.0)
    after = path[reached:]
    after = after[~np.isnan(after)]
    assert np.all(after >= entry - 1e-9), "breakeven not held after +1R"


def test_no_lookahead_truncation_invariant():
    rng = np.random.default_rng(2)
    c = 100 * np.cumprod(1 + rng.normal(0.001, 0.012, 150))
    df = _bars(c)
    full = TE.stop_path(df, 0, df["close"].iloc[0], df["close"].iloc[0]*0.95, "long")
    k = 100
    trunc = TE.stop_path(df.iloc[:k + 1], 0, df["close"].iloc[0],
                         df["close"].iloc[0]*0.95, "long")
    assert np.allclose(full[:k + 1], trunc, equal_nan=True, atol=1e-9)


def test_simulate_exit_detection_long():
    # rally then a sharp drop must trigger a trailing exit above the initial stop
    up = list(100 * np.cumprod(1 + np.full(40, 0.02)))
    down = list(up[-1] * np.cumprod(1 + np.full(20, -0.05)))
    df = _bars(up + down)
    # chandelier-only trail (n-bar structural trail is intentionally tighter and
    # tested elsewhere); a smooth rally should not stop it out, the -5% cascade should
    r = TE.simulate(df, 0, entry=df["close"].iloc[0], init_stop=df["close"].iloc[0]*0.97,
                    side="long", cfg=TrailConfig(n_bar=None, atr_mult=2.5, breakeven_R=None))
    assert r["reason"] == "trail"
    assert r["exit_price"] > df["close"].iloc[0]           # trailed above entry before exit
    assert r["exit_idx"] > 40                              # exited during the drop


def test_max_hold_time_exit():
    c = 100 * np.cumprod(1 + np.full(60, 0.001))
    df = _bars(c)
    r = TE.simulate(df, 0, df["close"].iloc[0], df["close"].iloc[0]*0.9, "long",
                    cfg=TrailConfig(atr_mult=None, n_bar=None, breakeven_R=None),
                    max_hold=10)
    assert r["reason"] == "time" and r["exit_idx"] == 10
