"""
test_data_layer.py — tests for the MarketDataManager → ... → FeatureStore pipeline.

Most tests use a FAKE manager (synthetic panel) for speed/determinism; a few hit
the real pipeline on cached data. The network update() path is NOT exercised here
(only its pure planner). Runs under pytest / run_tests.py.
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

import data_layer as DL
import factors as F


class _FakeCalendar:
    def __init__(self, sessions):
        self._s = pd.DatetimeIndex(sessions)

    def next_session(self, after):
        sub = self._s[self._s > pd.Timestamp(after)]
        return sub[0] if len(sub) else None


class _FakeManager:
    """Deterministic synthetic stand-in for MarketDataManager (no .version → the
    FeatureStore disk cache is disabled, keeping these tests fast/isolated)."""
    def __init__(self, n_days=300, syms=("A", "B", "C", "D")):
        idx = pd.bdate_range("2020-01-01", periods=n_days)
        rng = np.random.default_rng(0)
        self._close = pd.DataFrame(
            {s: 100 * np.cumprod(1 + rng.normal(0.0003 * (i + 1), 0.01, n_days))
             for i, s in enumerate(syms)}, index=idx)
        self._vol = pd.DataFrame({s: 1e6 * (i + 1) for i, s in enumerate(syms)}, index=idx)
        self.data_dir = "data"
        self.calendar = _FakeCalendar(idx)

    def close_panel(self): return self._close
    def universe(self): return list(self._close.columns)
    def as_of(self): return self._close.index[-1].date().isoformat()
    def context(self): return F.PanelContext(close=self._close, volume=self._vol)


# ── FeatureStore + FeatureCache ───────────────────────────────────────────────

def test_feature_store_scores_and_mem_cache():
    store = DL.FeatureStore(_FakeManager())
    s = store.get("momentum")
    assert s.min() >= 0.0 and s.max() <= 1.0
    n = store.cache_size()
    store.get("momentum")                          # cached → no growth
    assert store.cache_size() == n
    store.get("low_volatility")
    assert store.cache_size() == n + 1


def test_feature_store_frame_and_composite():
    store = DL.FeatureStore(_FakeManager())
    df = store.scores()
    assert set(df.columns) == set(F.FACTORS)
    comp = store.composite({"momentum": 1.0, "trend": 1.0})
    assert list(comp) == sorted(comp, reverse=True)
    assert comp.max() <= 1.0 and comp.min() >= 0.0


def test_feature_cache_roundtrip_and_version_keyed():
    tmp = Path(tempfile.mkdtemp())
    s = pd.Series([0.1, 0.9], index=["A", "B"])
    c1 = DL.FeatureCache("ver1", cache_dir=tmp)
    assert c1.get("momentum", 5) is None           # empty
    c1.put("momentum", 5, s)
    pd.testing.assert_series_equal(c1.get("momentum", 5), s)
    # A different version sees no cache (auto-invalidation).
    assert DL.FeatureCache("ver2", cache_dir=tmp).get("momentum", 5) is None
    # Disabled when version is None.
    assert DL.FeatureCache(None, cache_dir=tmp).get("momentum", 5) is None


# ── CorporateActionManager ────────────────────────────────────────────────────

def test_corp_action_clean_panel_no_flags():
    cam = DL.CorporateActionManager(_FakeManager())
    assert cam.is_adjusted is True
    panel, flags = cam.adjust()
    assert panel.equals(cam.manager.close_panel())
    assert flags == []
    assert DL.CorporateActionAdjuster is DL.CorporateActionManager   # alias kept


def test_corp_action_detects_split_like_jump():
    mgr = _FakeManager()
    mgr._close.iloc[150, 0] *= 2.0                  # +100% one-day jump on "A"
    cam = DL.CorporateActionManager(mgr)
    assert any(e["symbol"] == "A" for e in cam.detect())
    assert any(f["symbol"] == "A" for f in cam.flags())


# ── IncrementalUpdater (pure planner only — no network) ───────────────────────

def test_incremental_plan_full_when_no_data():
    up = DL.IncrementalUpdater(_FakeManager())
    assert up.plan(None, "2020-12-31") == ("full", None)


def test_incremental_plan_uptodate_and_incremental():
    mgr = _FakeManager()
    sessions = mgr.context().close.index
    last = sessions[-1]
    up = DL.IncrementalUpdater(mgr)
    assert up.plan(last, last)[0] == "uptodate"     # nothing after the last session
    earlier = sessions[-3]
    mode, start = up.plan(earlier, last)
    assert mode == "incremental"
    assert pd.Timestamp(start) == sessions[-2]      # next session after `earlier`


# ── Real pipeline end-to-end (cached data) ────────────────────────────────────

def test_real_pipeline_prepare():
    status = DL.DataPipeline().prepare()
    for k in ("version", "as_of", "n_symbols", "n_sessions", "validation",
              "is_clean", "corporate_actions", "features"):
        assert k in status
    assert status["n_symbols"] > 0 and status["n_sessions"] > 0
    assert len(status["version"]) == 12
    assert set(status["features"]) == set(F.FACTORS)


def test_real_incremental_plan_all_keys():
    plan = DL.IncrementalUpdater(DL.MarketDataManager()).plan_all()
    assert len(plan) > 0
    for sym, (mode, _start) in plan.items():
        assert mode in ("full", "incremental", "uptodate")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
