"""
test_risk.py — sanity tests for risk_analytics.py.

Analytic / monotonic checks on known inputs. Runs standalone or under pytest.
"""

import numpy as np
import pandas as pd

import risk_analytics as RA


def _eq(rets, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(rets) + 1)
    return pd.Series(np.concatenate([[1.0], np.cumprod(1.0 + np.asarray(rets))]), index=idx)


def test_var_is_positive_loss_and_cvar_worse():
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0, 0.01, 1000))
    v = RA.historical_var(r, 0.95)
    cv = RA.cvar(r, 0.95)
    assert v > 0 and cv > 0
    assert cv >= v                                  # expected shortfall >= VaR

def test_var_99_deeper_than_95():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0, 0.01, 2000))
    assert RA.historical_var(r, 0.99) >= RA.historical_var(r, 0.95)


def test_ulcer_zero_for_monotonic_rise():
    eq = _eq([0.001] * 300)                         # never draws down
    assert RA.ulcer_index(eq) < 1e-9
    dd = RA.drawdown_stats(eq)
    assert dd["current_drawdown"] == 0.0 or abs(dd["current_drawdown"]) < 1e-12
    assert dd["time_in_drawdown"] == 0.0


def test_vol_target_scale_unit_when_matched():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0, 0.01, 1000))
    realised = r.std(ddof=1) * np.sqrt(252)
    out = RA.vol_target_scale(r, target_ann_vol=realised)
    assert abs(out["scale"] - 1.0) < 1e-9


def test_atr_and_sizing():
    n = 60
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(np.linspace(100, 110, n), index=idx)
    high, low = close + 2, close - 2                 # constant 4-wide bars
    a = RA.atr(high, low, close, window=14)
    assert a is not None and a > 0
    size = RA.position_size_atr(1_000_000, 0.01, a, multiplier=2.0)
    assert size["units"] > 0
    # Bigger ATR -> smaller size for the same risk budget.
    smaller = RA.position_size_atr(1_000_000, 0.01, a * 2, multiplier=2.0)
    assert smaller["units"] < size["units"]


def test_drawdown_known_depth():
    eq = pd.Series([100, 120, 60, 90], index=pd.bdate_range("2020-01-01", periods=4))
    s = RA.drawdown_stats(eq)
    assert abs(s["max_drawdown"] - (-0.5)) < 1e-9


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
