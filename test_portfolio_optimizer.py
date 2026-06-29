"""
test_portfolio_optimizer.py — tests for the constraint-aware allocator.
"""

import numpy as np
import pandas as pd

from portfolio_optimizer import PortfolioOptimizer, Constraints


def _returns(n=300, syms=("A", "B", "C", "D", "E", "F")):
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(0)
    # different vols per name so inverse-vol / heat have something to bite on
    return pd.DataFrame({s: rng.normal(0.0005, 0.005 * (i + 1), n)
                         for i, s in enumerate(syms)}, index=idx)


_SECTORS = {"A": "Fin", "B": "Fin", "C": "Fin", "D": "IT", "E": "IT", "F": "Energy"}


def test_inverse_vol_downweights_risky():
    opt = PortfolioOptimizer(_returns(), _SECTORS,
                             Constraints(scheme="inverse_vol", max_position=1.0,
                                         sector_limit=1.0, correlation_limit=1.0,
                                         cash_buffer=0.0, target_vol=None))
    w = opt.optimize()["weights"]
    assert w["A"] > w["F"]                         # A is calmest, F is jumpiest


def test_max_position_capped():
    opt = PortfolioOptimizer(_returns(), _SECTORS,
                             Constraints(max_position=0.20, sector_limit=1.0,
                                         correlation_limit=1.0, cash_buffer=0.0,
                                         target_vol=None))
    res = opt.optimize()
    assert res["diagnostics"]["max_position"] <= 0.20 + 1e-6


def test_sector_limit_respected():
    opt = PortfolioOptimizer(_returns(), _SECTORS,
                             Constraints(scheme="equal", max_position=1.0,
                                         sector_limit=0.40, correlation_limit=1.0,
                                         cash_buffer=0.0, target_vol=None))
    res = opt.optimize()
    assert res["diagnostics"]["max_sector"] <= 0.40 + 1e-3


def test_cash_buffer_held():
    opt = PortfolioOptimizer(_returns(), _SECTORS,
                             Constraints(cash_buffer=0.10, target_vol=None,
                                         max_position=1.0, sector_limit=1.0,
                                         correlation_limit=1.0))
    res = opt.optimize()
    assert res["cash"] >= 0.10 - 1e-6
    assert abs(sum(res["weights"].values()) + res["cash"] - 1.0) < 1e-6


def test_portfolio_heat_derisks_to_target():
    opt = PortfolioOptimizer(_returns(), _SECTORS,
                             Constraints(scheme="equal", max_position=1.0,
                                         sector_limit=1.0, correlation_limit=1.0,
                                         cash_buffer=0.0, target_vol=0.05))
    res = opt.optimize()
    assert res["diagnostics"]["portfolio_vol"] <= 0.05 + 1e-6   # heat capped
    assert res["cash"] > 0.0                                     # ...by raising cash


def test_config_loads():
    c = Constraints.from_config()
    assert 0 < c.max_position <= 1 and 0 <= c.cash_buffer < 1


def test_empty_universe_degrades_to_valid_all_cash():
    # No candidates must NOT crash the pipeline: optimize() returns a schema-valid
    # all-cash payload (regression for the 'no symbols' → schema-crash path).
    import schemas
    rets = pd.DataFrame(index=pd.RangeIndex(10))          # rows, zero columns
    res = PortfolioOptimizer(rets, {}, Constraints.from_config()).optimize()
    assert res["weights"] == {} and res["cash"] == 1.0
    assert res["diagnostics"]["n_positions"] == 0
    payload = {"generated": "2026-06-29", "as_of": "2026-06-26", "candidates": [], **res}
    schemas.validate("optimizer.json", payload)           # must not raise


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
