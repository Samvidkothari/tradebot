"""
regime.py — Market-regime classifier (Research Engine).

Transparent, rule-based classification of the market's state from index closes
(NIFTY 50). Deliberately NOT a black box — every label is a simple, explainable
rule a human can audit, because an unexplainable regime call is worse than none.

Three independent axes are classified, then combined into a tag set:

  • Trend       : bull / bear / sideways   (price vs a long MA + the MA's slope)
  • Volatility  : high_volatility / low_volatility
                  (current short-window realised vol vs its own trailing-year
                   distribution — *relative*, so it adapts to the instrument)
  • Character   : trending / mean_reverting
                  (Kaufman efficiency ratio: net move / path length)

The tag set bridges to BaseStrategy.supported_regimes: a strategy is "compatible"
with the current market when its declared regimes intersect the live tags. This
is RESEARCH signal only — it never gates a live order (there is no live trading).
It surfaces *whether the market currently suits each strategy*, with the reason.

Parameters are config (module constants), not magic numbers buried in logic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TREND_MA        = 200     # long trend moving average (trading days)
SLOPE_LOOKBACK  = 20      # MA slope measured over this many days
VOL_WINDOW      = 20      # short window for "current" realised vol
VOL_HIST        = 252     # trailing window the current vol is ranked against
VOL_HIGH_PCTL   = 0.70    # current vol above this percentile of its year = "high"
ER_WINDOW       = 20      # efficiency-ratio window
ER_TREND_MIN    = 0.30    # efficiency ratio >= this = trending, else mean-reverting
TRADING_DAYS    = 252

# Canonical regime tags (must match BaseStrategy.supported_regimes vocabulary).
BULL, BEAR, SIDEWAYS = "bull", "bear", "sideways"
HIGH_VOL, LOW_VOL    = "high_volatility", "low_volatility"
TRENDING, MEANREV    = "trending", "mean_reverting"


def _ann_vol(closes: pd.Series, window: int) -> float | None:
    rets = closes.pct_change().dropna().iloc[-window:]
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS))


def efficiency_ratio(closes: pd.Series, window: int = ER_WINDOW) -> float | None:
    """Kaufman efficiency ratio over `window`: |net change| / total path length.
    1.0 = a perfectly straight move (strong trend); ~0 = lots of motion, no
    progress (choppy / mean-reverting)."""
    seg = closes.iloc[-(window + 1):]
    if len(seg) < window + 1:
        return None
    net = abs(seg.iloc[-1] - seg.iloc[0])
    path = seg.diff().abs().sum()
    if path == 0:
        return None
    return float(net / path)


def _vol_percentile(closes: pd.Series) -> tuple[float | None, float | None]:
    """Current short-window vol and its percentile rank within the trailing year."""
    daily = closes.pct_change()
    roll = daily.rolling(VOL_WINDOW).std(ddof=1) * np.sqrt(TRADING_DAYS)
    roll = roll.dropna()
    if len(roll) < 30:
        return None, None
    cur = float(roll.iloc[-1])
    hist = roll.iloc[-VOL_HIST:]
    pctl = float((hist < cur).mean())
    return cur, pctl


def classify(closes: pd.Series, as_of=None) -> dict:
    """Classify the market state as of `as_of` (default: last available day).
    Returns labels, the raw measures behind them, and a human-readable reason."""
    s = closes.dropna().sort_index()
    if as_of is not None:
        s = s[s.index <= pd.Timestamp(as_of)]
    out = {"as_of": s.index[-1].date().isoformat() if len(s) else None,
           "trend": None, "volatility": None, "character": None,
           "tags": [], "reason": "insufficient data", "measures": {}}
    if len(s) < TREND_MA + SLOPE_LOOKBACK:
        return out

    price = float(s.iloc[-1])
    ma = s.rolling(TREND_MA).mean()
    ma_now = float(ma.iloc[-1])
    ma_then = float(ma.iloc[-1 - SLOPE_LOOKBACK])
    slope = ma_now - ma_then

    if price > ma_now and slope > 0:
        trend = BULL
    elif price < ma_now and slope < 0:
        trend = BEAR
    else:
        trend = SIDEWAYS

    cur_vol, pctl = _vol_percentile(s)
    vol = None
    if pctl is not None:
        vol = HIGH_VOL if pctl >= VOL_HIGH_PCTL else LOW_VOL

    er = efficiency_ratio(s)
    character = None
    if er is not None:
        character = TRENDING if er >= ER_TREND_MIN else MEANREV

    tags = [t for t in (trend, vol, character) if t]
    out.update({
        "trend": trend, "volatility": vol, "character": character, "tags": tags,
        "measures": {
            "price": round(price, 1), "ma200": round(ma_now, 1),
            "ma_slope_20d": round(slope, 1),
            "ann_vol_20d": round(cur_vol, 4) if cur_vol is not None else None,
            "vol_percentile_1y": round(pctl, 2) if pctl is not None else None,
            "efficiency_ratio_20d": round(er, 2) if er is not None else None,
        },
        "reason": (f"Price {price:.0f} {'above' if price > ma_now else 'below'} "
                   f"200-day MA {ma_now:.0f} ({'rising' if slope > 0 else 'falling'}); "
                   f"20d vol at {int((pctl or 0)*100)}th pctl of the year; "
                   f"efficiency ratio {er:.2f}." if er is not None else "partial data"),
    })
    return out


def compatibility(supported_regimes, tags) -> dict:
    """How a strategy's declared regimes line up with the current market tags."""
    sup = set(supported_regimes)
    live = set(tags)
    matched = sorted(sup & live)
    return {"compatible": bool(matched), "matched": matched,
            "supported": sorted(sup), "missing": sorted(live - sup)}
