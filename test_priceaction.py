"""
test_priceaction.py — the mechanical price-action signal logic.

Pins the objective rules (ATR, confirmed fractal swings, and the trade generator)
so the strategy stays honest: entries respect R:R>=RR_MIN, sit in a valid price
order, and never look ahead (entry precedes exit). Pure logic on synthetic bars.
"""
import numpy as np
import pandas as pd

import priceaction as PA


def _ohlc(closes):
    c = np.asarray(closes, float)
    idx = pd.bdate_range("2022-01-01", periods=len(c))
    # small symmetric wicks around the close
    return pd.DataFrame({"open": c, "high": c * 1.004, "low": c * 0.996, "close": c}, index=idx)


def test_atr_positive_and_length():
    df = _ohlc(100 + np.cumsum(np.random.default_rng(0).normal(0, 1, 80)))
    a = PA.atr(df)
    assert len(a) == len(df) and np.all(a[PA.ATR_N:] > 0)


def test_confirmed_swings_are_lagged():
    # a clean peak at index 10 is confirmed L bars later, never earlier.
    c = np.concatenate([np.arange(1, 12), np.arange(10, 0, -1)]).astype(float)
    df = _ohlc(c)
    sh, sl = PA._confirmed_swings(df["high"].values, df["low"].values, PA.L)
    peak = int(np.nanargmax(np.where(np.isnan(sh), -1, sh)))
    assert peak >= 11 + PA.L - 1        # confirmation is lagged, not at the pivot bar


def test_generate_trades_shape_and_invariants():
    rng = np.random.default_rng(1)
    df = _ohlc(100 * np.cumprod(1 + rng.normal(0.0004, 0.012, 400)))
    trades = PA.generate_trades(df)
    assert isinstance(trades, list)
    for t in trades:
        assert t["entry_date"] < t["exit_date"]          # no look-ahead
        assert t["risk"] > 0
        if t["side"] == "long":
            assert t["sl"] < t["entry"] < t["tp"]         # valid long geometry
            assert (t["tp"] - t["entry"]) / (t["entry"] - t["sl"]) >= PA.RR_MIN - 1e-9
        else:
            assert t["tp"] < t["entry"] < t["sl"]         # valid short geometry
            assert (t["entry"] - t["tp"]) / (t["sl"] - t["entry"]) >= PA.RR_MIN - 1e-9


def test_too_short_series_no_trades():
    assert PA.generate_trades(_ohlc([100, 101, 102])) == []
