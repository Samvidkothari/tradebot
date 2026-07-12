"""
test_futures_data.py — the futures continuous/back-adjust engine (Phase 0).

Proves the correctness that the whole futures sleeve rests on, on deterministic
synthetic contracts (no network, no yfinance):

  • return preservation — within every contract's window the back-adjusted return
    equals that contract's own raw return (a return-based backtest sees no seam);
  • seam continuity / no fabricated jump — the roll day introduces no artificial
    return; the ratio method reproduces the true underlying series exactly under a
    multiplicative basis, the diff method under an additive basis;
  • newest segment = real prices (front contract left unadjusted);
  • roll-date logic (rolls before expiry, on an overlapping session);
  • honest failure on bad input (too few contracts / no overlap).
"""
import numpy as np
import pandas as pd
import pytest

import futures_data as F
from futures_data import Contract, build_continuous


# ── synthetic world: a hidden "true" price; each contract = true × (or +) basis ──

def _true_series(n=260, seed=0, start="2023-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    px = 100 * np.cumprod(1 + rng.normal(0.0003, 0.011, n))
    return pd.Series(px, index=idx)


def _contract(true, lo, hi, expiry, mult=1.0, add=0.0):
    """A contract quoting the true price over [lo, hi], offset by a constant
    multiplicative `mult` and/or additive `add` basis. OHLC = flat around close."""
    s = true[(true.index >= lo) & (true.index <= hi)]
    close = s * mult + add
    df = pd.DataFrame({"open": close, "high": close * 1.001,
                       "low": close * 0.999, "close": close})
    exp = pd.Timestamp(expiry)
    return Contract(symbol=f"C{exp:%y%m}", expiry=exp, df=df)


def _quarterly_ratio_contracts(true):
    # four overlapping quarterly contracts, increasing contango (multiplicative)
    return [
        _contract(true, "2023-01-02", "2023-04-14", "2023-03-31", mult=1.00),
        _contract(true, "2023-02-01", "2023-07-14", "2023-06-30", mult=1.02),
        _contract(true, "2023-05-01", "2023-10-13", "2023-09-29", mult=1.045),
        _contract(true, "2023-08-01", "2023-12-29", "2023-12-29", mult=1.07),
    ]


def test_ratio_backadjust_reproduces_true_series():
    true = _true_series()
    cont = build_continuous(_quarterly_ratio_contracts(true), method="ratio")
    mult_last = 1.07
    expected = (true.reindex(cont.index) * mult_last).dropna()
    got = cont["close"].reindex(expected.index)
    # under a pure multiplicative basis, ratio back-adjust == true × newest basis
    assert np.allclose(got.values, expected.values, rtol=1e-9, atol=1e-9)


def test_diff_backadjust_reproduces_true_series_additive():
    true = _true_series(seed=1)
    contracts = [
        _contract(true, "2023-01-02", "2023-04-14", "2023-03-31", add=0.0),
        _contract(true, "2023-02-01", "2023-07-14", "2023-06-30", add=5.0),
        _contract(true, "2023-05-01", "2023-10-13", "2023-09-29", add=9.0),
        _contract(true, "2023-08-01", "2023-12-29", "2023-12-29", add=12.0),
    ]
    cont = build_continuous(contracts, method="diff")
    expected = (true.reindex(cont.index) + 12.0).dropna()
    got = cont["close"].reindex(expected.index)
    assert np.allclose(got.values, expected.values, rtol=1e-9, atol=1e-7)


def test_returns_preserved_within_each_contract_window():
    true = _true_series(seed=2)
    contracts = _quarterly_ratio_contracts(true)
    cont = build_continuous(contracts, method="ratio")
    cont_ret = cont["close"].pct_change()
    for c in contracts:
        seg_dates = cont.index[cont["contract"] == c.symbol]
        # compare on interior dates of the segment (skip the first, no prior in-seg)
        for d in seg_dates[1:]:
            raw = c.df["close"]
            prev = raw.index[raw.index < d]
            if len(prev) == 0:
                continue
            raw_ret = raw.loc[d] / raw.loc[prev[-1]] - 1
            assert abs(cont_ret.loc[d] - raw_ret) < 1e-9


def test_no_fabricated_jump_at_roll_seam():
    true = _true_series(seed=3)
    cont = build_continuous(_quarterly_ratio_contracts(true), method="ratio")
    # the largest single-day return of the continuous series must not exceed the
    # largest true daily return (a bad stitch would spike far above it)
    cont_max = cont["close"].pct_change().abs().max()
    true_max = true.pct_change().abs().max()
    assert cont_max <= true_max + 1e-9


def test_newest_segment_is_unadjusted_real_prices():
    true = _true_series(seed=4)
    contracts = _quarterly_ratio_contracts(true)
    cont = build_continuous(contracts, method="ratio")
    newest = max(contracts, key=lambda c: c.expiry)
    seg = cont[cont["contract"] == newest.symbol]["close"]
    real = newest.df["close"].reindex(seg.index)
    assert np.allclose(seg.values, real.values, rtol=1e-12, atol=1e-9)


def test_roll_flags_and_order():
    true = _true_series(seed=5)
    contracts = _quarterly_ratio_contracts(true)
    cont = build_continuous(contracts, method="ratio")
    assert int(cont["roll"].sum()) == len(contracts) - 1        # one roll per seam
    # active contract changes in expiry order
    seen = cont["contract"].drop_duplicates().tolist()
    order = [c.symbol for c in sorted(contracts, key=lambda c: c.expiry)]
    assert seen == [s for s in order if s in seen]


def test_roll_happens_before_expiry_on_overlap():
    true = _true_series(seed=6)
    a = _contract(true, "2023-01-02", "2023-04-14", "2023-03-31", mult=1.0)
    b = _contract(true, "2023-02-01", "2023-07-14", "2023-06-30", mult=1.02)
    r = F._roll_date(a, b, F.ROLL_BUFFER)
    assert r <= a.expiry
    assert r in a.df.index and r in b.df.index                  # on an overlap session


def test_errors_on_bad_input():
    true = _true_series(seed=7)
    one = _contract(true, "2023-01-02", "2023-03-31", "2023-03-31")
    with pytest.raises(ValueError):
        build_continuous([one])                                  # < 2 contracts
    # no overlap → cannot define a roll ratio
    a = _contract(true, "2023-01-02", "2023-02-15", "2023-02-15", mult=1.0)
    b = _contract(true, "2023-06-01", "2023-07-14", "2023-06-30", mult=1.02)
    with pytest.raises(ValueError):
        build_continuous([a, b])


def test_quality_report_basic():
    true = _true_series(seed=8)
    cont = build_continuous(_quarterly_ratio_contracts(true), method="ratio")
    q = F.quality_report(cont, name="TEST")
    assert q["ok"] and q["rows"] > 100 and q["n_rolls"] == 3
    assert q["nan_pct"] == 0.0
