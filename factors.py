"""
factors.py — Modular factor library (Research Engine).

A BaseFeature plug-in architecture: each factor turns the price/volume panel into
a cross-sectionally **normalised** score in [0, 1] where 1 = most attractive
(direction-aware), so heterogeneous signals can be combined with weights into a
multi-factor composite — exactly the "combine multiple factors instead of relying
on a single indicator" goal.

HONEST DATA SCOPE — read this before adding factors:
We have **price + volume only** (yfinance daily OHLCV; the free Kite plan has no
fundamentals). So this library implements the factors that data can honestly
support: momentum, low-volatility, trend, short-term reversal, volatility
compression, and liquidity. The prompt's **fundamental** factors — Quality,
Value, Growth, ROE, ROCE, earnings/sales growth, delivery %, institutional
flows — are intentionally **NOT** implemented: we have no data for them and a
fabricated fundamental score is worse than none. They become available only when
a fundamentals feed is added; each is listed in UNAVAILABLE_FACTORS below.

Reuse: momentum and low-vol delegate to the pre-registered momentum.py / lowvol.py
signal logic (no rule reimplemented).

RESEARCH ONLY. Computing a multi-factor composite here is analysis, not a
tradeable strategy: a multi-factor *strategy* would need Phase 2B pre-registration
(thesis + spec committed before any backtest), and the equity strategy-class
budget is already closed (low-vol passed). This module ranks; it never trades.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
import pandas as pd

import lowvol
import momentum

# Fundamental factors we cannot honestly compute without a fundamentals feed.
UNAVAILABLE_FACTORS = [
    "Quality (ROE/ROCE)", "Value (P/E, P/B)", "Earnings/Sales Growth",
    "Operating Margin", "Delivery %", "Institutional Buying",
]


@dataclass
class PanelContext:
    """Everything a factor may need: aligned daily close and volume panels
    (index = dates, columns = symbols)."""
    close: pd.DataFrame
    volume: pd.DataFrame | None = None


# ── Plug-in contract ──────────────────────────────────────────────────────────

class BaseFeature(ABC):
    name: str = "base"
    description: str = ""
    direction: str = "high"     # "high" = larger raw is better; "low" = smaller is better

    @abstractmethod
    def raw(self, ctx: PanelContext, pos: int) -> pd.Series:
        """Raw factor value per symbol at integer index `pos` (NaN where the
        symbol is not rankable). Direction is applied later in score()."""

    def score(self, ctx: PanelContext, pos: int) -> pd.Series:
        """Cross-sectional rank score in [0, 1]; 1 = most attractive. Rank-based
        (robust to the fat tails of financial data) and direction-aware."""
        r = self.raw(ctx, pos).dropna()
        if len(r) < 2:
            return pd.Series(dtype=float)
        pct = r.rank(pct=True)                       # largest raw → ~1
        return pct if self.direction == "high" else (1.0 - pct + 1.0 / len(r))


# ── Price/volume factors (the data honestly supports these) ───────────────────

class MomentumFactor(BaseFeature):
    name, description, direction = "momentum", "12-1 cross-sectional momentum", "high"

    def raw(self, ctx, pos):
        return momentum.momentum_scores(ctx.close, pos)


class LowVolatilityFactor(BaseFeature):
    name, description, direction = "low_volatility", "60-day realized volatility (lower better)", "low"

    def raw(self, ctx, pos):
        return lowvol.vol_scores(ctx.close, pos)


class TrendFactor(BaseFeature):
    name, description, direction = "trend", "distance of price above its 200-day MA", "high"
    MA = 200

    def raw(self, ctx, pos):
        if pos < self.MA:
            return pd.Series(dtype=float)
        window = ctx.close.iloc[pos - self.MA + 1: pos + 1]
        ma = window.mean()
        price = ctx.close.iloc[pos]
        valid = ma.notna() & (ma > 0) & price.notna()
        return (price[valid] / ma[valid]) - 1.0


class ReversalFactor(BaseFeature):
    name, description, direction = "reversal", "5-day return (short-term mean reversion: buy losers)", "low"
    LOOKBACK = 5

    def raw(self, ctx, pos):
        if pos < self.LOOKBACK:
            return pd.Series(dtype=float)
        now, past = ctx.close.iloc[pos], ctx.close.iloc[pos - self.LOOKBACK]
        valid = now.notna() & past.notna() & (past > 0)
        return (now[valid] / past[valid]) - 1.0


class VolCompressionFactor(BaseFeature):
    name, description, direction = "vol_compression", "10d vol / 60d vol (compressed = primed)", "low"
    SHORT, LONG = 10, 60

    def raw(self, ctx, pos):
        if pos < self.LONG + 1:
            return pd.Series(dtype=float)
        rets = ctx.close.iloc[pos - self.LONG: pos + 1].pct_change().iloc[1:]
        short_v = rets.iloc[-self.SHORT:].std()
        long_v = rets.std()
        valid = (long_v > 0) & short_v.notna()
        return short_v[valid] / long_v[valid]


class LiquidityFactor(BaseFeature):
    name, description, direction = "liquidity", "20-day average traded value (close x volume)", "high"
    WINDOW = 20

    def raw(self, ctx, pos):
        if ctx.volume is None or pos < self.WINDOW:
            return pd.Series(dtype=float)
        c = ctx.close.iloc[pos - self.WINDOW + 1: pos + 1]
        v = ctx.volume.iloc[pos - self.WINDOW + 1: pos + 1]
        tv = (c * v).mean()
        return tv[tv.notna() & (tv > 0)]


# All implemented factors, by name.
FACTORS: dict[str, BaseFeature] = {
    f.name: f for f in (
        MomentumFactor(), LowVolatilityFactor(), TrendFactor(),
        ReversalFactor(), VolCompressionFactor(), LiquidityFactor(),
    )
}


# ── Multi-factor combination ──────────────────────────────────────────────────

def composite(ctx: PanelContext, pos: int, weights: dict[str, float]) -> pd.Series:
    """Weighted blend of factor scores → one composite score per symbol (only
    symbols scored by every weighted factor are kept, so the blend is fair)."""
    cols, total_w = {}, sum(abs(w) for w in weights.values()) or 1.0
    for name, w in weights.items():
        cols[name] = FACTORS[name].score(ctx, pos) * w
    df = pd.DataFrame(cols).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    return (df.sum(axis=1) / total_w).sort_values(ascending=False)
