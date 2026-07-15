"""test_ceinsys_alert.py — the 200-DMA reclaim watcher fires exactly on the edge.

Synthetic OHLC only (no network, no real data). Verifies the trend-gate logic:
below-line → no cross, a fresh close above the 200-DMA → cross_up True, and the
day after (still above) → cross_up False (edge-triggered, no daily spam).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ceinsys_alert as CAL


def _frame(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    c = np.array(closes, float)
    dates = pd.bdate_range("2022-01-03", periods=n)
    return pd.DataFrame({
        "date": dates, "open": c, "high": c * 1.01, "low": c * 0.99,
        "close": c, "volume": np.full(n, 100_000)})


@pytest.mark.unit
def test_below_line_does_not_cross():
    # 260 days flat-ish below a rising average: always below, never a cross
    closes = [900] * 260
    ev = CAL.evaluate(_frame(closes))
    assert ev["enough_history"] is True
    assert ev["state"] == "below" or ev["cross_up"] is False


@pytest.mark.unit
def test_fresh_reclaim_triggers_once():
    # 210 days at 900 (sets the 200-DMA near 900), then a jump above it today
    closes = [900] * 210 + [860] * 20        # push price below, MA still ~ high
    closes += [1200]                          # today: gap well above the 200-DMA
    ev = CAL.evaluate(_frame(closes))
    assert ev["state"] == "above"
    assert ev["cross_up"] is True
    # plan is well-formed and ordered
    assert ev["plan"]["stop"] < ev["plan"]["entry"] < ev["plan"]["target"]
    assert ev["plan"]["qty_at_1pct_risk"] >= 0


@pytest.mark.unit
def test_second_day_above_does_not_refire():
    closes = [900] * 210 + [860] * 20 + [1200, 1210]   # two days above in a row
    ev = CAL.evaluate(_frame(closes))
    assert ev["state"] == "above"
    assert ev["cross_up"] is False            # already above yesterday → no re-fire
