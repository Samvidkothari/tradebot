"""
test_feature_store.py — registry, metadata, feature versioning, incremental store.
"""

import numpy as np
import pandas as pd

import factors as F
import feature_store as FS


class _FakeManager:
    """Synthetic stand-in (no .version → disk cache disabled; in-memory reuse only)."""
    def __init__(self, n=300, syms=("A", "B", "C", "D")):
        idx = pd.bdate_range("2020-01-01", periods=n)
        rng = np.random.default_rng(0)
        self._c = pd.DataFrame({s: 100 * np.cumprod(1 + rng.normal(0.0003*(i+1), 0.01, n))
                                for i, s in enumerate(syms)}, index=idx)
        self._v = pd.DataFrame({s: 1e6*(i+1) for i, s in enumerate(syms)}, index=idx)

    def context(self): return F.PanelContext(close=self._c, volume=self._v)


def test_registry_metadata_and_inputs():
    reg = FS.FeatureRegistry()
    assert set(reg.names()) == set(F.FACTORS)
    md = reg.metadata("momentum")
    assert md["direction"] == "high" and md["inputs"] == ["close"]
    assert len(md["version"]) == 8
    assert reg.metadata("liquidity")["inputs"] == ["close", "volume"]


def test_feature_version_stable_distinct_and_definition_sensitive():
    reg = FS.FeatureRegistry()
    v = reg.feature_version("momentum")
    assert reg.feature_version("momentum") == v               # stable
    assert reg.feature_version("momentum") != reg.feature_version("trend")  # distinct

    class _MomBumped(F.MomentumFactor):
        version = "99"                                         # changed definition
    assert FS._feature_version(_MomBumped()) != FS._feature_version(F.MomentumFactor())


def test_store_materialize_is_incremental():
    store = FS.FeatureStore(_FakeManager())
    r1 = store.materialize()
    assert set(r1["computed"]) == set(F.FACTORS) and r1["reused"] == []
    r2 = store.materialize()                                   # everything now cached (memory)
    assert r2["computed"] == [] and set(r2["reused"]) == set(F.FACTORS)
    assert r1["n_features"] == len(F.FACTORS)


def test_store_get_scores_composite():
    store = FS.FeatureStore(_FakeManager())
    s = store.get("momentum")
    assert s.min() >= 0.0 and s.max() <= 1.0
    assert set(store.scores().columns) == set(F.FACTORS)
    comp = store.composite({"momentum": 1.0, "trend": 1.0})
    assert list(comp) == sorted(comp, reverse=True)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
