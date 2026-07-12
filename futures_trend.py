"""futures_trend.py — time-series (trend) momentum signal for futures (Phase 1).

The signal half of the FUTURES_TRENDS_SCOPE.md Phase-1 single-/multi-market
prototype. Pure, per-market logic on a continuous series' daily closes — no I/O,
no orders, no look-ahead. The portfolio wiring, governor, and costs live in
backtest_futures_trend.py.

Canonical, deliberately un-clever rules (Moskowitz–Ooi–Pedersen style TSMOM),
locked before any backtest:

  • DIRECTION — sign of the trailing MOM_LOOKBACK return: long a market whose
    price is higher than a year ago, short if lower. A slow trend, nothing more.
  • SIZE — VOLATILITY TARGETING (Varma's "size to the risk state", per market):
    weight = TARGET_VOL / realized_vol, capped at WEIGHT_CAP, so a calm market
    gets more and a wild one less — equal *risk*, not equal *capital*.
  • target_position(t) uses only data through t; the backtest shifts it one day
    (enter next open) so there is no look-ahead.

Parameters are FIXED (pre-registered in strategies/SPEC_futures_trend.md); they
must not be tuned to results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── Pre-registered parameters (do not tune to results) ────────────────────────
MOM_LOOKBACK = 252     # trailing sessions for the trend sign (~12 months)
VOL_WINDOW   = 60      # sessions for realized vol (the sizing denominator)
TARGET_VOL   = 0.15    # per-market annualized vol target
WEIGHT_CAP   = 2.0     # cap |weight| so a quiet market can't dominate
TRADING_DAYS = 252

WARMUP = MOM_LOOKBACK + 1


def trend_sign(close: pd.Series, lookback: int = MOM_LOOKBACK) -> pd.Series:
    """+1 / −1 / 0 by the sign of the trailing-`lookback` return, per date
    (data through that date only)."""
    ret = close / close.shift(lookback) - 1.0
    return np.sign(ret).fillna(0.0)


def realized_vol(close: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """Annualized realized vol of daily returns over a trailing window."""
    r = close.pct_change()
    return r.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS)


def vol_weight(close: pd.Series, window: int = VOL_WINDOW,
               target: float = TARGET_VOL, cap: float = WEIGHT_CAP) -> pd.Series:
    """Volatility-target weight = target / realized_vol, clipped to [0, cap].
    NaN/zero-vol → 0 (stand aside rather than divide by zero)."""
    rv = realized_vol(close, window)
    w = target / rv.replace(0.0, np.nan)
    return w.clip(upper=cap).fillna(0.0)


def target_position(df: pd.DataFrame, lookback: int = MOM_LOOKBACK,
                    window: int = VOL_WINDOW, target: float = TARGET_VOL,
                    cap: float = WEIGHT_CAP) -> pd.Series:
    """Signed, vol-scaled target weight per date for one market: direction (trend
    sign) × size (vol target). The backtest shifts this by one day (no look-ahead).
    Returns 0 during warmup and wherever inputs are missing."""
    close = df["close"].astype(float)
    pos = trend_sign(close, lookback) * vol_weight(close, window, target, cap)
    pos.iloc[:WARMUP] = 0.0
    return pos.fillna(0.0)
