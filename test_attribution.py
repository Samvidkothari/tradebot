"""
test_attribution.py — sanity tests for attribution.py.

Uses a tiny fake strategy + synthetic panel so the attribution identities are
exact and checkable. Runs standalone or under pytest.
"""

import numpy as np
import pandas as pd

import attribution as A


class _FakeStrategy:
    """Holds a fixed list of names every rebalance."""
    def __init__(self, names, warmup_pos=1):
        self._names, self.warmup_pos, self.top_n = names, warmup_pos, len(names)

    def select(self, panel, day):
        return [n for n in self._names if n in panel.columns]


def _panel(n_months=6, syms=None):
    syms = syms or ["A", "B", "C", "D"]
    # One trading day per month-start so each month is one rebalance period.
    idx = pd.bdate_range("2020-01-01", periods=n_months, freq="MS")
    rng = np.random.default_rng(0)
    data = {s: 100 * np.cumprod(1 + rng.normal(0.01, 0.03, n_months)) for s in syms}
    return pd.DataFrame(data, index=idx)


def test_contribution_total_reconciles():
    panel = _panel()
    strat = _FakeStrategy(["A", "B", "C", "D"])
    hc = A.holding_contributions(strat, panel)
    # Sum of per-symbol contributions == reported total.
    assert abs(sum(hc["by_symbol"].values()) - hc["total"]) < 1e-9
    # Sector roll-up sums to the same total.
    assert abs(sum(hc["by_sector"].values()) - hc["total"]) < 1e-9


def test_brinson_identity():
    """Allocation + selection + interaction must equal active return."""
    panel = _panel(n_months=8)
    strat = _FakeStrategy(["A", "B"])              # holds a subset → real active return
    b = A.brinson(strat, panel)
    t = b["total"]
    decomposed = t["allocation"] + t["selection"] + t["interaction"]
    assert abs(decomposed - t["active_return"]) < 1e-9
    assert abs(t["active_return"] - (b["portfolio_return"] - b["benchmark_return"])) < 1e-9


def test_holding_whole_universe_zero_active():
    """If the portfolio IS the equal-weight universe, active return ~ 0."""
    panel = _panel()
    strat = _FakeStrategy(["A", "B", "C", "D"])     # holds everything
    b = A.brinson(strat, panel)
    assert abs(b["total"]["active_return"]) < 1e-9


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
