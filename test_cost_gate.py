"""
test_cost_gate.py — the intraday cost gate (Framework pillar B).

Checks the gate does its job: a thin-edge strategy (the frozen-intraday case)
FAILS, a genuinely strong edge PASSES, cost rises with size, and the gate is
monotonic — making costs bigger can only turn a PASS into a FAIL, never the
reverse. Pure logic.
"""
import numpy as np
import pandas as pd
import pytest

import cost_gate as CG
from cost_gate import GateInputs, evaluate


def _inputs(mean_gross, sd_gross, n=400, risk=0.01, tpy=750, size=0.01, seed=0):
    rng = np.random.default_rng(seed)
    g = pd.Series(rng.normal(mean_gross, sd_gross, n))
    r = pd.Series(np.full(n, risk))
    return GateInputs(gross_ret=g, risk=r, trades_per_year=tpy, size_fraction=size)


def test_thin_edge_fails():
    # gross edge ~ 3 bps/trade — below the ~13 bps round-trip cost: the frozen case
    res = evaluate(_inputs(mean_gross=0.0003, sd_gross=0.01))
    assert res["passed"] is False
    assert res["net_expectancy"] < res["gross_expectancy"]


def test_strong_edge_passes():
    # gross edge ~ 40 bps/trade, low noise → clears cost with margin + Sharpe
    res = evaluate(_inputs(mean_gross=0.0040, sd_gross=0.006, risk=0.01))
    assert res["passed"] is True
    assert all(res["checks"].values())


def test_cost_rises_with_size():
    assert CG.round_trip_cost(0.05) > CG.round_trip_cost(0.01)


def test_monotonic_bigger_size_cannot_help():
    small = evaluate(_inputs(mean_gross=0.0040, sd_gross=0.006, size=0.01))
    big = evaluate(_inputs(mean_gross=0.0040, sd_gross=0.006, size=0.20))
    # larger size = larger cost = smaller net expectancy
    assert big["net_expectancy"] < small["net_expectancy"]
    # a pass cannot become "more passing" by adding cost
    if not small["passed"]:
        assert not big["passed"]


def test_gate_reasons_present():
    res = evaluate(_inputs(mean_gross=0.001, sd_gross=0.01))
    assert set(res["checks"]) == {"net_expectancy_positive",
                                  "gross_beats_cost_margin", "net_sharpe_floor"}
    assert "VERDICT" in CG.format_report(res, name="x")
