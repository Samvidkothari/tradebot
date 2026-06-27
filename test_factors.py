"""
test_factors.py — sanity tests for the factor library (factors.py).

Synthetic panels with an obvious winner so normalisation and direction can't
silently drift. Runs standalone or under pytest.
"""

import numpy as np
import pandas as pd

import factors as F


def _ctx(n_days=300, n_syms=5):
    idx = pd.bdate_range("2020-01-01", periods=n_days)
    syms = [f"S{i}" for i in range(n_syms)]
    # S0 trends strongly up; the rest drift mildly. Deterministic.
    close = {}
    for i, s in enumerate(syms):
        drift = 0.0015 if i == 0 else 0.0001 * i
        close[s] = pd.Series(100 * (1 + drift) ** np.arange(n_days), index=idx)
    cdf = pd.DataFrame(close)
    vol = pd.DataFrame({s: pd.Series(1e6 * (i + 1), index=idx) for i, s in enumerate(syms)})
    return F.PanelContext(close=cdf, volume=vol), syms


def test_scores_in_unit_interval():
    ctx, _ = _ctx()
    pos = len(ctx.close) - 1
    for feat in F.FACTORS.values():
        s = feat.score(ctx, pos)
        if len(s):
            assert s.min() >= 0.0 and s.max() <= 1.0, feat.name


def test_momentum_winner_ranks_top():
    ctx, syms = _ctx()
    pos = len(ctx.close) - 1
    s = F.MomentumFactor().score(ctx, pos)
    assert s.idxmax() == "S0"                      # strongest trend → best momentum


def test_direction_low_inverts():
    """Low-vol factor: the calmest name should score highest."""
    ctx, _ = _ctx()
    pos = len(ctx.close) - 1
    raw = F.LowVolatilityFactor().raw(ctx, pos)
    score = F.LowVolatilityFactor().score(ctx, pos)
    if len(raw) >= 2:
        assert score.idxmax() == raw.idxmin()       # lowest vol → highest score


def test_liquidity_uses_volume():
    ctx, syms = _ctx()
    pos = len(ctx.close) - 1
    s = F.LiquidityFactor().score(ctx, pos)
    assert s.idxmax() == syms[-1]                   # highest volume → most liquid


def test_composite_sorted_and_weighted():
    ctx, _ = _ctx()
    pos = len(ctx.close) - 1
    comp = F.composite(ctx, pos, {"momentum": 1.0, "trend": 1.0})
    assert list(comp) == sorted(comp, reverse=True)  # descending
    assert comp.max() <= 1.0 and comp.min() >= 0.0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
