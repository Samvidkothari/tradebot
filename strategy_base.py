"""
strategy_base.py — Plug-in strategy architecture (Research Engine).

Both equity strategies already share one shape: each exposes
`target_portfolio(panel, date)` and is run by an identical monthly-rebalance,
turnover-cost engine (duplicated today across backtest_lowvol.py and
backtest_momentum.py). This module factors that out:

  • BaseStrategy            — the plug-in contract every strategy implements
                             (metadata + economic rationale + supported regimes +
                             a `select()` method that returns the names to hold).
  • MonthlyRebalanceEngine — the ONE shared backtest loop (equal-weight,
                             turnover-aware cost), strategy-agnostic.
  • LowVolStrategy / MomentumStrategy — thin plug-ins that REUSE the existing,
                             pre-registered signal logic in lowvol.py / momentum.py
                             (no rule is reimplemented or changed here).
  • REGISTRY               — name -> strategy instance, so research tooling can
                             iterate strategies generically.

IMPORTANT — pre-registration integrity:
The canonical backtest_lowvol.py / backtest_momentum.py are deliberately left
UNTOUCHED — their committed verdicts came from that exact code. This engine is a
proven-equivalent parallel path for research tooling: test_strategy_base.py
asserts it reproduces those backtests' equity curves bit-for-bit. New strategies
should be built as plug-ins here rather than by copy-pasting a backtest.

Research only — no orders, no data fetching, no parameter tuning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

# Reuse the IDENTICAL cost model the pre-registered backtests use (now in config).
from config import COST_ENTRY, COST_EXIT
import lowvol
import momentum


# ── Plug-in contract ──────────────────────────────────────────────────────────

class BaseStrategy(ABC):
    """Every strategy is a plug-in implementing this contract. Subclasses set the
    class attributes (config, not hard-coded magic) and implement select()."""

    name: str = "base"
    label: str = "Base Strategy"
    top_n: int = 15
    warmup_pos: int = 0                 # min integer index before first rebalance
    supported_regimes: tuple = ()       # declared; consumed by the future regime engine
    economic_rationale: str = ""

    @abstractmethod
    def select(self, panel_raw: pd.DataFrame, day) -> list[str]:
        """Names to hold as of `day` (equal-weight). Delegates to the strategy's
        pre-registered signal logic — never reimplements it."""

    def as_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "top_n": self.top_n,
                "warmup_pos": self.warmup_pos,
                "supported_regimes": list(self.supported_regimes),
                "economic_rationale": self.economic_rationale}


# ── Shared backtest engine ────────────────────────────────────────────────────

class MonthlyRebalanceEngine:
    """The single monthly-rebalance, equal-weight, turnover-cost loop. Identical
    to backtest_lowvol.run_lowvol / backtest_momentum.run_momentum; the only
    per-strategy inputs are warmup_pos, top_n, and select()."""

    def __init__(self, cost_entry: float = COST_ENTRY,
                 cost_exit: float = COST_EXIT, starting: float = 1.0):
        self.cost_entry = cost_entry
        self.cost_exit = cost_exit
        self.starting = starting

    def rebalance_dates(self, panel: pd.DataFrame, warmup_pos: int):
        first_of_month = {}
        for d in panel.index:
            key = (d.year, d.month)
            if key not in first_of_month:
                first_of_month[key] = d
        return [d for d in first_of_month.values()
                if panel.index.get_loc(d) >= warmup_pos]

    def run(self, strategy: BaseStrategy, panel_raw: pd.DataFrame):
        """Returns (equity Series, n_changes, history) — same contract as the
        pre-registered run_lowvol / run_momentum."""
        panel_val = panel_raw.ffill()
        rebals = self.rebalance_dates(panel_raw, strategy.warmup_pos)
        if not rebals:
            return pd.Series(dtype=float), 0, []

        first_rebal = rebals[0]
        rebal_set = set(rebals)
        top_n = strategy.top_n

        shares, cash, prev_held = {}, self.starting, set()
        entries = exits = 0
        history, equity = [], {}

        days = panel_raw.index[panel_raw.index >= first_rebal]
        for day in days:
            px = panel_val.loc[day]

            if day in rebal_set:
                pv = cash + sum(sh * px[s] for s, sh in shares.items())

                target = strategy.select(panel_raw, day)
                names = set(shares) | set(target)
                cur_val = {s: shares.get(s, 0) * px[s] for s in names}

                tgt_each0 = pv / top_n
                bought = sum(max((tgt_each0 if s in target else 0) - cur_val[s], 0)
                             for s in names)
                sold = sum(max(cur_val[s] - (tgt_each0 if s in target else 0), 0)
                           for s in names)
                cost = bought * self.cost_entry + sold * self.cost_exit

                invest = pv - cost
                tgt_each = invest / top_n

                new_shares = {}
                for s in target:
                    price = px[s]
                    if pd.notna(price) and price > 0:
                        new_shares[s] = tgt_each / price

                new_held = set(new_shares)
                entries += len(new_held - prev_held)
                exits += len(prev_held - new_held)

                invested = tgt_each * len(new_shares)
                cash = pv - invested - cost
                shares = new_shares
                prev_held = new_held

                history.append(dict(date=day, n_holdings=len(new_shares),
                                     bought=bought, sold=sold, cost=cost))

            pv = cash + sum(sh * px[s] for s, sh in shares.items())
            equity[day] = pv

        return pd.Series(equity).sort_index(), entries + exits, history


# ── Concrete plug-ins (reuse pre-registered signal logic) ─────────────────────

class LowVolStrategy(BaseStrategy):
    name = "lowvol"
    label = "Low-Volatility (15 lowest-vol)"
    top_n = lowvol.TOP_N
    warmup_pos = lowvol.VOL_LOOKBACK
    supported_regimes = ("low_volatility", "sideways", "bear", "bull")
    economic_rationale = ("Low-volatility anomaly: calmer stocks earn higher "
                          "risk-adjusted returns than CAPM predicts; defensive in "
                          "drawdowns.")

    def select(self, panel_raw, day):
        return lowvol.target_portfolio(panel_raw, day, top_n=self.top_n)


class MomentumStrategy(BaseStrategy):
    name = "momentum"
    label = "Momentum (12-1, top-15)"
    top_n = momentum.TOP_N
    warmup_pos = momentum.LOOKBACK
    supported_regimes = ("bull", "trending")
    economic_rationale = ("Cross-sectional 12-1 momentum: recent relative winners "
                          "keep winning over 1–12 months; works in trending markets, "
                          "vulnerable to sharp reversals.")

    def select(self, panel_raw, day):
        return momentum.target_portfolio(panel_raw, day, top_n=self.top_n)


REGISTRY: dict[str, BaseStrategy] = {
    s.name: s for s in (LowVolStrategy(), MomentumStrategy())
}
