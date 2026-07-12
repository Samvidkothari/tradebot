"""
test_momentum_governed.py — pins the Varma-governed momentum backtest
(SPEC_momentum_governed.md).

Guards the invariants that make it a legitimate overlay and not a re-tuned
strategy: exposure never exceeds 1.0 and stays within the sizer's band; at full
exposure the governed engine reproduces the canonical `run_momentum` equity
curve byte-for-byte (so selection/cost logic is unchanged); and the governor does
not make drawdown worse. Uses the real cached panel (as other backtest tests do).
"""
import numpy as np
import pandas as pd
import pytest

import backtest_momentum_governed as G
from backtest_momentum import run_momentum, equity_metrics
from data_io import load_panel
import varma_riskstate as V


@pytest.fixture(scope="module")
def panel():
    p, _ = load_panel()
    return p


def test_exposure_within_sizer_band(panel):
    _, _, hist, expo = G.run_momentum_governed(panel)
    assert hist, "expected rebalances on the real panel"
    vals = list(expo.values())
    assert all(V.FLOOR - 1e-9 <= f <= 1.0 for f in vals), "exposure must stay in [FLOOR, 1.0]"


def test_never_increases_exposure(panel):
    _, _, _, expo = G.run_momentum_governed(panel)
    assert max(expo.values()) <= 1.0 + 1e-9


def test_reproduces_baseline_at_full_exposure(panel, monkeypatch):
    # Force exposure to 1.0 everywhere → governed math must equal canonical run_momentum.
    monkeypatch.setattr(G, "_exposure", lambda day: 1.0)
    gov_eq, gov_changes, _, _ = G.run_momentum_governed(panel)
    base_eq, base_changes, _ = run_momentum(panel)
    assert gov_changes == base_changes
    aligned = gov_eq.reindex(base_eq.index)
    assert np.allclose(aligned.values, base_eq.values, rtol=1e-9, atol=1e-12), \
        "at full exposure the governed curve must match the pre-registered baseline"


def test_governor_does_not_worsen_drawdown(panel):
    gov_eq, _, _, _ = G.run_momentum_governed(panel)
    base_eq, _, _ = run_momentum(panel)
    gov, base = equity_metrics(gov_eq), equity_metrics(base_eq)
    assert abs(gov["max_dd"]) <= abs(base["max_dd"]) + 1e-9, \
        "a ≤1.0 exposure overlay must not deepen the drawdown"


def test_evaluate_flags_a_worse_book():
    # Sanity on the verdict logic: identical books can't be 'better', so C2 fails.
    m = {"cagr": 0.10, "max_dd": -0.20}
    v = G.evaluate(m, m, m, m, None, None)
    assert v["c1"] is False and v["passed"] is False
