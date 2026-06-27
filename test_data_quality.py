"""
test_data_quality.py — sanity tests for data_quality.py pure checks.

Synthetic series with injected defects so each detector is exercised. Runs
standalone or under pytest.
"""

import numpy as np
import pandas as pd

import data_quality as DQ


def _close(vals):
    return pd.Series(vals, index=pd.bdate_range("2020-01-01", periods=len(vals)))


def test_extreme_moves_detected():
    c = _close([100, 101, 100, 150, 151])          # one ~49% jump
    assert DQ.count_extreme_moves(c) == 1
    assert DQ.count_extreme_moves(_close([100, 101, 102, 103])) == 0


def test_zero_volume_count():
    v = pd.Series([100, 0, 0, 50, 0])
    assert DQ.count_zero_volume(v) == 3
    assert DQ.count_zero_volume(None) == 0


def test_nonpositive_prices():
    assert DQ.count_nonpositive(_close([100, 0, -5, 10])) == 2
    assert DQ.count_nonpositive(_close([1, 2, 3])) == 0


def test_duplicate_and_sorted_dates():
    d = pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-02"])
    assert DQ.has_duplicate_dates(pd.Series(d)) is True
    assert DQ.is_sorted(pd.Series(d)) is True
    rev = pd.to_datetime(["2020-01-03", "2020-01-01"])
    assert DQ.is_sorted(pd.Series(rev)) is False


def test_staleness_days():
    # 2020-01-01 (Wed) to 2020-01-08 (Wed) = 5 business days.
    assert DQ.staleness_days("2020-01-01", "2020-01-08") == 5


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
