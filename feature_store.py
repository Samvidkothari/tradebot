"""
feature_store.py — registry-driven feature store: calculate → store → version → reuse.

Upgrades the simple per-run cache into a proper store so research stops
re-deriving the same features every run:

  • FeatureRegistry — every feature + metadata (description, direction, inputs) and
                      a `feature_version` = hash of the feature's DEFINITION (its
                      source + declared version). Change the logic → the version
                      changes → its cache invalidates automatically.
  • FeatureCache    — persistent on disk, keyed by (data_version, feature_version),
                      so EITHER a data change OR a feature-logic change invalidates
                      the right entries (and nothing else).
  • FeatureStore    — compute-on-miss, reuse-on-hit (memory → disk → compute);
                      `materialize()` is an INCREMENTAL refresh that computes only
                      what's missing and reports computed-vs-reused.

READ-ONLY / research only; no orders.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import date
from pathlib import Path

import pandas as pd

import factors as F

RESULTS_DIR = Path(__file__).parent / "results"

CACHE_DIR = Path(__file__).parent / "data" / "_feature_cache"


def _feature_version(feat) -> str:
    """Definition hash — changes iff the feature's source or declared version does."""
    src = inspect.getsource(type(feat))
    return hashlib.sha1(f"{getattr(feat, 'version', '1')}\n{src}".encode()).hexdigest()[:8]


# ── Registry + metadata ───────────────────────────────────────────────────────

class FeatureRegistry:
    """Catalog of available features + their metadata and definition versions."""

    def __init__(self, features: dict | None = None):
        self._features = dict(features if features is not None else F.FACTORS)
        self._ver: dict[str, str] = {}

    def names(self) -> list[str]:
        return list(self._features)

    def get(self, name: str):
        return self._features[name]

    def feature_version(self, name: str) -> str:
        if name not in self._ver:
            self._ver[name] = _feature_version(self._features[name])
        return self._ver[name]

    def metadata(self, name: str) -> dict:
        f = self._features[name]
        return {"name": name, "description": f.description, "direction": f.direction,
                "inputs": list(getattr(f, "inputs", ("close",))),
                "version": self.feature_version(name)}

    def all_metadata(self) -> dict:
        return {n: self.metadata(n) for n in self._features}


# ── Persistent cache (data_version × feature_version keyed) ───────────────────

class FeatureCache:
    """Disk cache keyed by (data_version, feature_version, factor, pos)."""

    def __init__(self, data_version: str | None, cache_dir: Path = CACHE_DIR):
        self.data_version = data_version
        self.dir = Path(cache_dir)
        self.enabled = data_version is not None
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, factor: str, feature_version: str, pos: int) -> Path:
        return self.dir / f"{self.data_version}_{feature_version}_{factor}_{pos}.pkl"

    def get(self, factor: str, feature_version: str, pos: int):
        if not self.enabled:
            return None
        p = self._path(factor, feature_version, pos)
        return pd.read_pickle(p) if p.exists() else None

    def put(self, factor: str, feature_version: str, pos: int, series: pd.Series):
        if self.enabled:
            series.to_pickle(self._path(factor, feature_version, pos))

    def invalidate_stale(self) -> int:
        """Delete cache files from OTHER data versions. Returns count removed."""
        if not self.enabled or not self.dir.exists():
            return 0
        removed = 0
        for f in self.dir.glob("*.pkl"):
            if not f.name.startswith(f"{self.data_version}_"):
                f.unlink(); removed += 1
        return removed


# ── Store ─────────────────────────────────────────────────────────────────────

class FeatureStore:
    """Factor scores with memory + disk caching, version-keyed for correctness."""

    def __init__(self, manager, only=None, registry: FeatureRegistry | None = None):
        self.manager = manager
        if registry is None:
            feats = {k: v for k, v in F.FACTORS.items() if (only is None or k in only)}
            registry = FeatureRegistry(feats)
        self.registry = registry
        self.factors = {n: registry.get(n) for n in registry.names()}   # back-compat
        self.cache = FeatureCache(getattr(manager, "version", None))
        self._ctx = None
        self._mem: dict = {}

    # context / position --------------------------------------------------------
    def _context(self):
        if self._ctx is None:
            self._ctx = self.manager.context()
        return self._ctx

    def _pos(self, as_of) -> int:
        idx = self._context().close.index
        if as_of is None:
            return len(idx) - 1
        loc = idx.get_indexer([pd.Timestamp(as_of)], method="ffill")[0]
        return max(int(loc), 0)

    # lookup --------------------------------------------------------------------
    def get(self, factor: str, as_of=None) -> pd.Series:
        pos = self._pos(as_of)
        fv = self.registry.feature_version(factor)
        key = (factor, fv, pos)
        if key in self._mem:                                   # 1) memory
            return self._mem[key]
        disk = self.cache.get(factor, fv, pos)                 # 2) disk (version-keyed)
        if disk is not None:
            self._mem[key] = disk
            return disk
        s = self.factors[factor].score(self._context(), pos)   # 3) compute
        self._mem[key] = s
        self.cache.put(factor, fv, pos, s)
        return s

    def scores(self, as_of=None) -> pd.DataFrame:
        return pd.DataFrame({n: self.get(n, as_of) for n in self.registry.names()})

    def composite(self, weights: dict, as_of=None) -> pd.Series:
        return F.composite(self._context(), self._pos(as_of), weights)

    # incremental refresh -------------------------------------------------------
    def materialize(self, as_of=None) -> dict:
        """Compute every feature for `as_of`, skipping any already cached
        (memory or disk). Returns {computed, reused} — the incremental record."""
        pos = self._pos(as_of)
        computed, reused = [], []
        for name in self.registry.names():
            fv = self.registry.feature_version(name)
            cached = ((name, fv, pos) in self._mem
                      or self.cache.get(name, fv, pos) is not None)
            self.get(name, as_of)                              # ensure materialised
            (reused if cached else computed).append(name)
        return {"as_of": self._context().close.index[pos].date().isoformat(),
                "data_version": getattr(self.manager, "version", None),
                "computed": computed, "reused": reused,
                "n_features": len(self.registry.names())}

    def invalidate(self) -> int:
        """Drop on-disk cache from other data versions."""
        self._mem.clear()
        return self.cache.invalidate_stale()

    def cache_size(self) -> int:
        return len(self._mem)


def main():
    """Materialise the store and write results/feature_store.json for the dashboard."""
    import schemas
    from data_layer import MarketDataManager        # lazy → avoid import cycle

    RESULTS_DIR.mkdir(exist_ok=True)
    store = FeatureStore(MarketDataManager())
    mat = store.materialize()
    payload = {
        "generated": date.today().isoformat(),
        "as_of": mat["as_of"],
        "data_version": mat["data_version"],
        "n_features": mat["n_features"],
        "materialize": {"computed": mat["computed"], "reused": mat["reused"]},
        "features": store.registry.all_metadata(),
    }
    (RESULTS_DIR / "feature_store.json").write_text(
        json.dumps(schemas.validate("feature_store.json", payload), indent=2))
    print(f"  Feature store: {mat['n_features']} features · "
          f"{len(mat['computed'])} computed / {len(mat['reused'])} reused · "
          f"data {mat['data_version']} → results/feature_store.json")


if __name__ == "__main__":
    main()
