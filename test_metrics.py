"""
test_metrics.py — sanity tests for the research-analytics layer (metrics.py).

Deterministic checks on known inputs so the math can't silently drift. Runs
standalone (`python test_metrics.py`) or under pytest. No network, no data files.
"""

import math

import numpy as np
import pandas as pd

import metrics as M


def _curve(daily_rets, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(daily_rets) + 1)
    eq = pd.Series(np.concatenate([[1.0], np.cumprod(1.0 + np.asarray(daily_rets))]),
                   index=idx)
    return eq


def test_constant_growth():
    """A curve that only ever rises: zero drawdown, no down days."""
    eq = _curve([0.001] * 252)               # +0.1%/day for ~1 year
    assert M.max_drawdown(eq) == 0.0
    assert M.profit_factor(M.daily_returns(eq)) is None   # no losing days
    assert M.win_rate(M.daily_returns(eq)) == 1.0
    assert M.cagr(eq) > 0


def test_known_drawdown():
    """Up 10%, then down 50% from the peak → max drawdown is exactly -50%."""
    eq = pd.Series([100, 110, 55, 60],
                   index=pd.bdate_range("2020-01-01", periods=4))
    assert abs(M.max_drawdown(eq) - (-0.5)) < 1e-9


def test_sharpe_sign_and_zero_vol():
    rng = np.random.default_rng(1)
    pos = pd.Series(rng.normal(0.0008, 0.005, 500))      # positive drift
    assert M.sharpe(pos, rf=0.0) > 0
    flat = pd.Series([0.0] * 50)                          # zero variance → undefined
    assert M.sharpe(flat) is None


def test_beta_of_self_is_one():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0, 0.01, 300))
    beta, alpha = M.beta_alpha(r, r, rf=0.0)
    assert abs(beta - 1.0) < 1e-9
    assert abs(alpha) < 1e-6                              # no excess vs itself


def test_calmar_and_recovery_consistency():
    eq = _curve(list(np.linspace(0.002, -0.001, 300)))
    c, mdd = M.cagr(eq), M.max_drawdown(eq)
    if c is not None and mdd:
        assert abs(M.calmar(eq) - c / abs(mdd)) < 1e-9


def test_monte_carlo_shape():
    rng = np.random.default_rng(3)
    r = pd.Series(rng.normal(0.0004, 0.01, 500))
    mc = M.monte_carlo(r, n_sims=200, seed=0)
    assert mc["cagr_p5"] <= mc["cagr_p50"] <= mc["cagr_p95"]
    assert mc["maxdd_p5"] <= mc["maxdd_p50"] <= mc["maxdd_p95"]
    assert 0.0 <= mc["prob_negative_cagr"] <= 1.0


def test_tear_sheet_keys():
    eq = _curve([0.0005] * 300)
    ts = M.tear_sheet(eq, bench_equity=eq)
    for k in ("cagr", "sharpe", "sortino", "calmar", "max_drawdown",
              "beta", "alpha", "information_ratio", "profit_factor"):
        assert k in ts


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
