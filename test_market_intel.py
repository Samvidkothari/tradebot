"""
test_market_intel.py — surveillance filter, circuit detection, summary shape.
"""

import numpy as np
import pandas as pd

import market_intel as MI


def test_surveillance_filter_drops_flagged(monkeypatch=None):
    # with empty ASM/GSM slots (default), nothing is filtered
    syms = ["RELIANCE", "ITC", "TCS"]
    assert MI.filter_universe(syms) == syms
    assert MI.flagged_symbols() <= set()           # empty by default (config slots)


def test_circuit_events_flags_big_moves():
    idx = pd.bdate_range("2020-01-01", periods=60)
    s = pd.Series(100.0, index=idx)
    s.iloc[30] = 130.0                              # +30% one-day move
    panel = pd.DataFrame({"X": s})
    ev = MI.circuit_events(panel, band=0.20)
    assert any(e["symbol"] == "X" for e in ev)
    assert MI.circuit_events(panel, band=0.50) == []   # none above 50%


def test_summary_shape():
    s = MI.summary()
    for k in ("expiries", "sectors", "surveillance", "circuits", "corporate_actions",
              "n_symbols"):
        assert k in s
    assert "next_weekly" in s["expiries"] and "next_monthly" in s["expiries"]
    assert s["n_symbols"] > 0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
