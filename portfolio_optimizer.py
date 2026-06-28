"""
portfolio_optimizer.py — constraint-aware allocation (research lens).

Turns a set of selected stocks into a diversified target allocation:

  base weights (equal / inverse-vol)
    → cap max position
    → cap sector exposure
    → penalise highly-correlated names
    → renormalise
    → hold a cash buffer
    → de-risk to a portfolio-heat (volatility) target → more cash

Constraints are config (optimizer.json). Heuristic iterative projection — the
diagnostics report the *realised* max-position / max-sector / portfolio vol so any
residual is visible.

RESEARCH ONLY — produces a target allocation for analysis; it does NOT change the
pre-registered equal-weight low-vol paper book, and places no orders. Reuses the
maths in portfolio_analyzer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import portfolio_analyzer as PA

CONFIG_PATH = Path(__file__).parent / "optimizer.json"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class Constraints:
    scheme: str = "inverse_vol"        # "equal" | "inverse_vol"
    max_position: float = 0.10
    sector_limit: float = 0.35
    correlation_limit: float = 0.85
    cash_buffer: float = 0.05
    target_vol: float | None = 0.12    # portfolio "heat" cap (annualised); None = off

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH):
        cfg = {k: v for k, v in json.loads(Path(path).read_text()).items()
               if not k.startswith("_")}
        return cls(**cfg)


class PortfolioOptimizer:
    def __init__(self, returns: pd.DataFrame, sectors: dict,
                 constraints: Constraints | None = None):
        self.returns = returns.dropna(how="all", axis=1)
        self.symbols = list(self.returns.columns)
        self.sectors = sectors
        self.c = constraints or Constraints()
        self.vols = self.returns.std() * np.sqrt(PA.TRADING_DAYS)
        self.cov = PA.annualized_cov(self.returns)

    # ── weighting schemes ──────────────────────────────────────────────────────
    def _base(self) -> pd.Series:
        n = len(self.symbols)
        if self.c.scheme == "equal" or n == 0:
            return pd.Series(1.0 / n if n else 0.0, index=self.symbols)
        w = PA.inverse_vol_weights(self.vols.loc[self.symbols].to_numpy())
        return pd.Series(w, index=self.symbols)

    # ── constraint projections ────────────────────────────────────────────────
    @staticmethod
    def _cap_positions(w: pd.Series, cap: float) -> pd.Series:
        w = w.copy()
        for _ in range(100):
            over = w[w > cap + 1e-12]
            if over.empty:
                break
            excess = (over - cap).sum()
            w[over.index] = cap
            under = w[w < cap - 1e-12]
            if under.empty or under.sum() == 0:
                break
            w[under.index] += excess * under / under.sum()
        return w

    def _cap_sectors(self, w: pd.Series, limit: float) -> pd.Series:
        w = w.copy()
        sec = pd.Series({s: self.sectors.get(s, "Other") for s in w.index})
        for _ in range(50):
            sw = w.groupby(sec).sum()
            over = sw[sw > limit + 1e-9]
            if over.empty:
                break
            freed = 0.0
            for s in over.index:
                names = sec[sec == s].index
                scale = limit / sw[s]
                freed += w[names].sum() * (1 - scale)
                w[names] *= scale
            under = w[sec.isin(sw[sw < limit - 1e-9].index)]
            if under.empty or under.sum() == 0:
                break
            w[under.index] += freed * under / under.sum()
        return w

    def _penalise_correlation(self, w: pd.Series, limit: float) -> pd.Series:
        if len(w) < 2:
            return w
        corr = self.returns[w.index].corr()
        w = w.copy()
        for s in w.index:
            others = [o for o in w.index if o != s]
            mx = corr.loc[s, others].max()
            if pd.notna(mx) and mx > limit:
                w[s] *= max(0.0, 1.0 - (mx - limit) / (1.0 - limit))
        return w

    # ── full pipeline ─────────────────────────────────────────────────────────
    def optimize(self) -> dict:
        if not self.symbols:
            return {"error": "no symbols"}
        w = self._base()
        # iterate the position/sector projections so both bind together
        for _ in range(5):
            w = self._cap_positions(w, self.c.max_position)
            w = self._cap_sectors(w, self.c.sector_limit)
        w = self._penalise_correlation(w, self.c.correlation_limit)
        w = w / w.sum()                                  # fully invested baseline
        w = w * (1.0 - self.c.cash_buffer)               # cash buffer

        cov = self.cov.loc[w.index, w.index].to_numpy()
        heat = PA.portfolio_vol(w.to_numpy(), cov)
        if self.c.target_vol and heat > self.c.target_vol:   # portfolio-heat de-risk
            w *= self.c.target_vol / heat
            heat = PA.portfolio_vol(w.to_numpy(), cov)

        sec = pd.Series({s: self.sectors.get(s, "Other") for s in w.index})
        sector_w = w.groupby(sec).sum().sort_values(ascending=False)
        weights = w.sort_values(ascending=False)
        return {
            "scheme": self.c.scheme,
            "weights": {s: round(float(x), 4) for s, x in weights.items()},
            "sector_of": {s: sec[s] for s in weights.index},
            "sectors": {s: round(float(x), 4) for s, x in sector_w.items()},
            "cash": round(float(1.0 - w.sum()), 4),
            "diagnostics": {
                "portfolio_vol": round(float(heat), 4),
                "target_vol": self.c.target_vol,
                "max_position": round(float(weights.max()), 4),
                "max_sector": round(float(sector_w.iloc[0]), 4),
                "effective_n": round(PA.effective_n(w.to_numpy()), 2),
                "n_positions": int((w > 1e-6).sum()),
            },
            "constraints": {
                "max_position": self.c.max_position, "sector_limit": self.c.sector_limit,
                "correlation_limit": self.c.correlation_limit,
                "cash_buffer": self.c.cash_buffer, "target_vol": self.c.target_vol,
            },
        }


def main():
    import schemas
    from data_layer import MarketDataManager
    from feature_store import FeatureStore
    from multifactor import MultiFactorRanker
    from universe import UniverseManager

    RESULTS_DIR.mkdir(exist_ok=True)
    mgr = MarketDataManager()
    ranker, _ = MultiFactorRanker.from_model(FeatureStore(mgr))
    candidates = [d["symbol"] for d in ranker.top(15)]       # rank → then allocate
    rets = mgr.close_panel()[candidates].pct_change().dropna().tail(252)
    opt = PortfolioOptimizer(rets, UniverseManager().SECTOR_MAP, Constraints.from_config())
    res = opt.optimize()

    payload = {"generated": date.today().isoformat(), "as_of": mgr.as_of(),
               "candidates": candidates, **res}
    (RESULTS_DIR / "optimizer.json").write_text(
        json.dumps(schemas.validate("optimizer.json", payload), indent=2))
    d = res["diagnostics"]
    tv = opt.c.target_vol
    target = f" (target {tv*100:.0f}%)" if tv else ""
    print(f"  Optimizer ({res['scheme']}): {d['n_positions']} positions, "
          f"cash {res['cash']*100:.0f}%, vol {d['portfolio_vol']*100:.1f}%{target}, "
          f"max pos {d['max_position']*100:.1f}%, max sector {d['max_sector']*100:.0f}% "
          f"→ results/optimizer.json")


if __name__ == "__main__":
    main()
