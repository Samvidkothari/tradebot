"""
test_multifactor.py — tests for the config-driven multi-factor ranker.
"""

import numpy as np
import pandas as pd

import factors as F
import feature_store as FS
from multifactor import MultiFactorRanker, load_models


class _FakeManager:
    def __init__(self, n=300, syms=("A", "B", "C", "D", "E")):
        idx = pd.bdate_range("2020-01-01", periods=n)
        rng = np.random.default_rng(0)
        self._c = pd.DataFrame({s: 100*np.cumprod(1+rng.normal(0.0004*(i+1), 0.01, n))
                                for i, s in enumerate(syms)}, index=idx)
        self._v = pd.DataFrame({s: 1e6*(i+1) for i, s in enumerate(syms)}, index=idx)

    def context(self): return F.PanelContext(close=self._c, volume=self._v)


def _store():
    return FS.FeatureStore(_FakeManager())


def test_weighted_ranking_descending_and_bounded():
    r = MultiFactorRanker(_store(), {"momentum": 1.0, "low_volatility": 1.0,
                                     "trend": 1.0, "liquidity": 1.0})
    df = r.scores()
    assert "composite" in df.columns
    assert list(df["composite"]) == sorted(df["composite"], reverse=True)
    assert df["composite"].min() >= 0.0 and df["composite"].max() <= 1.0


def test_top_returns_breakdown():
    r = MultiFactorRanker(_store(), {"momentum": 2.0, "trend": 1.0})
    top = r.top(3)
    assert len(top) == 3
    assert set(top[0]["factors"]) == {"momentum", "trend"}     # per-factor breakdown
    assert top[0]["composite"] >= top[-1]["composite"]


def test_unknown_factor_rejected():
    try:
        MultiFactorRanker(_store(), {"not_a_factor": 1.0})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_config_default_model_loads():
    cfg = load_models()
    name = cfg["default"]
    m = cfg["models"][name]
    assert set(m["weights"]) <= set(F.FACTORS)                 # weights reference real factors
    assert "momentum" in m["weights"] and "liquidity" in m["weights"]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
