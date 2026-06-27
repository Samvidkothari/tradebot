"""
test_strategy_base.py — REGRESSION proof for the BaseStrategy refactor.

The whole point of the refactor is that it changes NO behaviour. These tests
assert that MonthlyRebalanceEngine reproduces the pre-registered backtests'
equity curves and position-change counts bit-for-bit. If they ever diverge, the
refactor is unsafe and the build fails — which is exactly what we want.

Runs standalone (`python test_strategy_base.py`) or under pytest. Needs the
cached data panel in data/ (run fetch_data.py first).
"""

import pandas as pd

from backtest_lowvol import load_panel, run_lowvol
from backtest_momentum import run_momentum
from strategy_base import MonthlyRebalanceEngine, REGISTRY


def _assert_identical(old, new, label):
    eq_old, ch_old, _ = old
    eq_new, ch_new, _ = new
    assert ch_old == ch_new, f"{label}: position-changes differ ({ch_old} vs {ch_new})"
    # Same dates, same values to the floating-point bit.
    pd.testing.assert_series_equal(eq_old, eq_new, check_exact=True,
                                   obj=f"{label} equity curve")


def test_lowvol_engine_matches_pre_registered():
    panel_raw, _ = load_panel()
    engine = MonthlyRebalanceEngine()
    _assert_identical(run_lowvol(panel_raw),
                      engine.run(REGISTRY["lowvol"], panel_raw), "lowvol")


def test_momentum_engine_matches_pre_registered():
    panel_raw, _ = load_panel()
    engine = MonthlyRebalanceEngine()
    _assert_identical(run_momentum(panel_raw),
                      engine.run(REGISTRY["momentum"], panel_raw), "momentum")


def test_registry_metadata():
    for name, strat in REGISTRY.items():
        assert strat.name == name
        assert strat.top_n > 0
        assert strat.supported_regimes          # every strategy declares its regimes
        assert strat.economic_rationale          # ...and its rationale


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
