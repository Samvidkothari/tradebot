"""
test_regime.py — sanity tests for the regime classifier (regime.py).

Synthetic series where the correct label is unambiguous, so the rules can't
silently drift. Runs standalone (`python test_regime.py`) or under pytest.
"""

import numpy as np
import pandas as pd

import regime as R


def _series(values, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def test_steady_uptrend_is_bull_trending():
    # 400 days rising smoothly: above a rising 200MA, near-straight path.
    s = _series(100 * (1.0005 ** np.arange(400)))
    c = R.classify(s)
    assert c["trend"] == R.BULL
    assert c["character"] == R.TRENDING            # high efficiency ratio
    assert R.BULL in c["tags"]


def test_steady_downtrend_is_bear():
    s = _series(100 * (0.9995 ** np.arange(400)))
    c = R.classify(s)
    assert c["trend"] == R.BEAR


def test_choppy_flat_is_sideways_meanreverting():
    # Clean oscillation around 100 (alternating +/-2): zero net drift, long path
    # → efficiency ratio ~0 → mean-reverting, and flat → sideways.
    vals = 100 + 2 * (np.arange(400) % 2 * 2 - 1)         # 98, 102, 98, 102, ...
    c = R.classify(_series(vals))
    assert c["character"] == R.MEANREV                    # low efficiency ratio
    assert c["trend"] == R.SIDEWAYS                       # flat vs its own MA

def test_insufficient_data_is_safe():
    c = R.classify(_series(np.arange(50)))
    assert c["trend"] is None and c["tags"] == []


def test_compatibility_intersects():
    comp = R.compatibility(("bull", "trending"), ["bull", "low_volatility", "trending"])
    assert comp["compatible"] is True
    assert comp["matched"] == ["bull", "trending"]
    none = R.compatibility(("bear",), ["bull", "trending"])
    assert none["compatible"] is False


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
