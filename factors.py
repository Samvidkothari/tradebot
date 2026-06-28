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
    "ROE", "ROCE", "EPS Growth", "Sales Growth",            # "future" fundamentals
    "Quality", "Value (P/E, P/B)", "Operating Margin",
    "Delivery %", "Institutional Buying",
]

# Market BREADTH is a single market-level number (e.g. % of names above their
# 200-day MA), not a per-stock cross-sectional score — it lives in the regime
# engine (regime.py), not in this cross-sectional factor library.


@dataclass
class PanelContext:
    """Everything a factor may need. close/volume are required by most factors;
    high/low feed range factors (ATR/ADX); benchmark (index close) feeds
    relative-strength / beta / correlation; sectors (symbol→sector) feeds sector
    strength. Missing optional inputs → the dependent factor returns empty (the
    feature simply isn't scored), never an error."""
    close: pd.DataFrame
    volume: pd.DataFrame | None = None
    high: pd.DataFrame | None = None
    low: pd.DataFrame | None = None
    benchmark: pd.Series | None = None      # index close, aligned to close.index
    sectors: dict | None = None             # symbol -> sector


# ── Plug-in contract ──────────────────────────────────────────────────────────

class BaseFeature(ABC):
    name: str = "base"
    description: str = ""
    direction: str = "high"     # "high" = larger raw is better; "low" = smaller is better
    inputs: tuple = ("close",)  # panels this feature reads (metadata for the store)
    version: str = "1"          # bump when the definition changes (cache invalidation)

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
    inputs = ("close", "volume")
    WINDOW = 20

    def raw(self, ctx, pos):
        if ctx.volume is None or pos < self.WINDOW:
            return pd.Series(dtype=float)
        c = ctx.close.iloc[pos - self.WINDOW + 1: pos + 1]
        v = ctx.volume.iloc[pos - self.WINDOW + 1: pos + 1]
        tv = (c * v).mean()
        return tv[tv.notna() & (tv > 0)]


# ── Statistical factors (close-only) ──────────────────────────────────────────

class VolatilityFactor(BaseFeature):
    name, description, direction = "volatility", "20-day annualised realized vol", "low"
    W = 20

    def raw(self, ctx, pos):
        if pos < self.W + 1:
            return pd.Series(dtype=float)
        rets = ctx.close.iloc[pos - self.W: pos + 1].pct_change().iloc[1:]
        v = rets.std() * (252 ** 0.5)
        return v[v.notna()]


class ZScoreFactor(BaseFeature):
    name, description, direction = "zscore", "price z-score vs 20-day mean (low = oversold)", "low"
    W = 20

    def raw(self, ctx, pos):
        if pos < self.W:
            return pd.Series(dtype=float)
        win = ctx.close.iloc[pos - self.W + 1: pos + 1]
        std = win.std()
        z = (ctx.close.iloc[pos] - win.mean()) / std
        return z[(std > 0) & z.notna()]


class TrendPersistenceFactor(BaseFeature):
    name = "trend_persistence"
    description = "Kaufman efficiency ratio (20d): trend straightness"
    direction = "high"
    W = 20

    def raw(self, ctx, pos):
        if pos < self.W + 1:
            return pd.Series(dtype=float)
        seg = ctx.close.iloc[pos - self.W: pos + 1]
        net = (seg.iloc[-1] - seg.iloc[0]).abs()
        path = seg.diff().abs().sum()
        return net[path > 0] / path[path > 0]


# ── Technical factors (volume / range) ────────────────────────────────────────

class RelativeVolumeFactor(BaseFeature):
    name = "relative_volume"
    description = "today's volume vs its 20-day average (attention)"
    direction, inputs = "high", ("volume",)
    W = 20

    def raw(self, ctx, pos):
        if ctx.volume is None or pos < self.W:
            return pd.Series(dtype=float)
        avg = ctx.volume.iloc[pos - self.W + 1: pos + 1].mean()
        rv = ctx.volume.iloc[pos] / avg
        return rv[(avg > 0) & rv.notna()]


class ATRFactor(BaseFeature):
    name = "atr"
    description = "ATR(14) as % of price (true-range volatility)"
    direction, inputs = "low", ("high", "low", "close")
    W = 14

    def raw(self, ctx, pos):
        if ctx.high is None or ctx.low is None or pos < self.W + 1:
            return pd.Series(dtype=float)
        out = {}
        for s in ctx.close.columns:
            h = ctx.high[s].iloc[pos - self.W: pos + 1]
            lo = ctx.low[s].iloc[pos - self.W: pos + 1]
            c = ctx.close[s].iloc[pos - self.W: pos + 1]
            prev = c.shift(1)
            tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
            atr = tr.dropna().iloc[-self.W:].mean()
            px = ctx.close[s].iloc[pos]
            if pd.notna(atr) and pd.notna(px) and px > 0:
                out[s] = atr / px
        return pd.Series(out, dtype=float)


class ADXFactor(BaseFeature):
    name, description = "adx", "ADX(14): trend strength"
    direction, inputs = "high", ("high", "low", "close")
    W = 14

    def raw(self, ctx, pos):
        if ctx.high is None or ctx.low is None or pos < 2 * self.W + 1:
            return pd.Series(dtype=float)
        out = {}
        n = 2 * self.W + 1
        for s in ctx.close.columns:
            h = ctx.high[s].iloc[pos - n + 1: pos + 1]
            lo = ctx.low[s].iloc[pos - n + 1: pos + 1]
            c = ctx.close[s].iloc[pos - n + 1: pos + 1]
            if h.isna().any() or lo.isna().any() or c.isna().any():
                continue
            up, dn = h.diff(), -lo.diff()
            plus_dm = ((up > dn) & (up > 0)) * up
            minus_dm = ((dn > up) & (dn > 0)) * dn
            prev = c.shift(1)
            tr = pd.concat([h - lo, (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
            atr = tr.rolling(self.W).mean().replace(0, float("nan"))
            pdi = 100 * plus_dm.rolling(self.W).mean() / atr
            mdi = 100 * minus_dm.rolling(self.W).mean() / atr
            dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
            adx = dx.rolling(self.W).mean().iloc[-1]
            if pd.notna(adx):
                out[s] = float(adx)
        return pd.Series(out, dtype=float)


# ── Market / relative factors (need the index benchmark or sectors) ───────────

class RelativeStrengthFactor(BaseFeature):
    name = "relative_strength"
    description = "63-day return minus the index return (vs NIFTY)"
    direction, inputs = "high", ("close", "benchmark")
    W = 63

    def raw(self, ctx, pos):
        if ctx.benchmark is None or pos < self.W:
            return pd.Series(dtype=float)
        base = ctx.close.iloc[pos - self.W]
        sr = ctx.close.iloc[pos] / base - 1
        br = ctx.benchmark.iloc[pos] / ctx.benchmark.iloc[pos - self.W] - 1
        return sr[base.notna() & (base > 0)] - br


class BetaFactor(BaseFeature):
    name = "beta"
    description = "120-day beta vs NIFTY (low = defensive)"
    direction, inputs = "low", ("close", "benchmark")
    W = 120

    def raw(self, ctx, pos):
        if ctx.benchmark is None or pos < self.W + 1:
            return pd.Series(dtype=float)
        sret = ctx.close.iloc[pos - self.W: pos + 1].pct_change().iloc[1:]
        bret = ctx.benchmark.iloc[pos - self.W: pos + 1].pct_change().iloc[1:]
        var_b = bret.var()
        if not var_b or var_b <= 0:
            return pd.Series(dtype=float)
        beta = sret.apply(lambda col: col.cov(bret) / var_b)
        return beta[beta.notna()]


class CorrelationFactor(BaseFeature):
    name = "correlation"
    description = "120-day return correlation with NIFTY (low = diversifier)"
    direction, inputs = "low", ("close", "benchmark")
    W = 120

    def raw(self, ctx, pos):
        if ctx.benchmark is None or pos < self.W + 1:
            return pd.Series(dtype=float)
        sret = ctx.close.iloc[pos - self.W: pos + 1].pct_change().iloc[1:]
        bret = ctx.benchmark.iloc[pos - self.W: pos + 1].pct_change().iloc[1:]
        corr = sret.apply(lambda col: col.corr(bret))
        return corr[corr.notna()]


class SectorStrengthFactor(BaseFeature):
    name = "sector_strength"
    description = "strength of the stock's sector (63-day avg sector return)"
    direction, inputs = "high", ("close", "sectors")
    W = 63

    def raw(self, ctx, pos):
        if not ctx.sectors or pos < self.W:
            return pd.Series(dtype=float)
        import collections
        base = ctx.close.iloc[pos - self.W]
        ret = (ctx.close.iloc[pos] / base - 1)[base.notna() & (base > 0)]
        bysec = collections.defaultdict(list)
        for sym, r in ret.items():
            sec = ctx.sectors.get(sym)
            if sec:
                bysec[sec].append(r)
        strength = {sec: sum(v) / len(v) for sec, v in bysec.items()}
        out = {sym: strength[ctx.sectors[sym]] for sym in ret.index
               if ctx.sectors.get(sym) in strength}
        return pd.Series(out, dtype=float)


# All implemented factors, by name.
FACTORS: dict[str, BaseFeature] = {
    f.name: f for f in (
        # technical
        MomentumFactor(), TrendFactor(), ATRFactor(), ADXFactor(),
        RelativeStrengthFactor(), RelativeVolumeFactor(),
        # statistical
        LowVolatilityFactor(), VolatilityFactor(), ZScoreFactor(),
        BetaFactor(), CorrelationFactor(), TrendPersistenceFactor(),
        ReversalFactor(), VolCompressionFactor(),
        # market
        SectorStrengthFactor(), LiquidityFactor(),
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
