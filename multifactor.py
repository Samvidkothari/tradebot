"""
multifactor.py — config-driven multi-factor ranking (research lens).

Instead of one strategy = one signal, blend several factor scores into a weighted
composite and rank:  factors → weighted score → ranking → top stocks. Models
(weights + universe) live in factor_models.json (config data, not code), and
scores come from the cached FeatureStore (fast reloads).

RESEARCH ONLY — a stock-selection lens, NOT a pre-registered tradeable strategy
(that would need Phase 2B pre-registration with one fixed weight set committed
before any backtest; weighted scores are easy to overfit). Places no orders.

Usage:  python multifactor.py [model]
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

CONFIG_PATH = Path(__file__).parent / "factor_models.json"
RESULTS_DIR = Path(__file__).parent / "results"


def load_models(path: Path = CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text())


class MultiFactorRanker:
    """Weighted blend of factor scores → ranked top stocks (with the per-factor
    breakdown, so a ranking is explainable)."""

    def __init__(self, store, weights: dict, universe: str | None = None,
                 description: str = ""):
        self.store = store
        self.weights = {k: float(v) for k, v in weights.items()}
        self.universe = universe
        self.description = description
        unknown = set(self.weights) - set(store.factors)
        if unknown:
            raise ValueError(f"weights reference unknown factors: {sorted(unknown)}")

    @classmethod
    def from_model(cls, store, name: str | None = None, path: Path = CONFIG_PATH):
        cfg = load_models(path)
        name = name or cfg.get("default")
        m = cfg["models"][name]
        return cls(store, m["weights"], universe=m.get("universe"),
                   description=m.get("description", "")), name

    def _universe_symbols(self) -> set | None:
        if not self.universe:
            return None
        from universe import UniverseManager
        return set(UniverseManager().resolve(self.universe))

    def scores(self, as_of=None) -> pd.DataFrame:
        """DataFrame indexed by symbol: each weighted factor's score + composite,
        sorted by composite descending."""
        cols = {name: self.store.get(name, as_of) for name in self.weights}
        df = pd.DataFrame(cols).dropna()
        syms = self._universe_symbols()
        if syms is not None:
            df = df.loc[df.index.isin(syms)]
        total = sum(abs(w) for w in self.weights.values()) or 1.0
        df["composite"] = sum(df[n] * w for n, w in self.weights.items()) / total
        return df.sort_values("composite", ascending=False)

    def top(self, n: int = 15, as_of=None) -> list[dict]:
        df = self.scores(as_of).head(n)
        return [{"symbol": sym,
                 "composite": round(float(row["composite"]), 3),
                 "factors": {f: round(float(row[f]), 3) for f in self.weights}}
                for sym, row in df.iterrows()]


def main(model_name: str | None = None):
    import schemas
    from data_layer import MarketDataManager
    from feature_store import FeatureStore

    RESULTS_DIR.mkdir(exist_ok=True)
    mgr = MarketDataManager()
    store = FeatureStore(mgr)
    ranker, name = MultiFactorRanker.from_model(store, model_name)
    top = ranker.top(15)
    payload = {
        "generated": date.today().isoformat(),
        "as_of": mgr.as_of(),
        "model": name,
        "description": ranker.description,
        "universe": ranker.universe,
        "weights": ranker.weights,
        "top": top,
    }
    (RESULTS_DIR / "multifactor.json").write_text(
        json.dumps(schemas.validate("multifactor.json", payload), indent=2))
    print(f"  Multi-factor '{name}' ({ranker.universe}): top "
          + ", ".join(d["symbol"] for d in top[:5])
          + f" → results/multifactor.json")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
