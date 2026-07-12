"""episodic_pivot.py — Episodic-Pivot ignition signal + sell-into-strength exit.

The daily-bar, mechanical half of Pradeep Bonde's Episodic Pivots (EP) playbook,
turned into OBJECTIVE, no-look-ahead rules so it can be judged after costs rather
than eyeballed. This module is LAYER 1 (selection) + LAYER 4 (exit) of the
combined Bonde+Varma design (see PLAYBOOK.md). The Varma layers (regime gate +
fractional-Kelly sizing) are applied on top, at the portfolio level, in
backtest_episodic_pivot.py — they need index context, not per-symbol data.

  Layer 1 — IGNITION (Bonde "trade what's in play"). An EP fires on day t when
    all three hold, using data through t only:
      1. Relative-volume surge : volume[t] >= RVOL_MULT × avg(volume, prior VOL_LOOKBACK)
      2. Thrust                : close[t]/close[t-1] − 1 >= THRUST_MIN
      3. Fresh breakout        : close[t] is the highest close of the last HIGH_LOOKBACK
    Entry is the NEXT day's open (t+1) — the signal is known at t's close, so
    there is no look-ahead. Long only (the cash book's EP is a long-side event).

  Layer 4 — SELL INTO STRENGTH (Bonde + Varma agree here). Magnitude moves mean-
    revert, so book half the position into the first spike and let the rest run:
      • initial stop = the ignition bar's low (risk = (entry−stop)/entry)
      • take HALF off at entry × (1 + TP1); move the remainder's stop to breakeven
      • trail the remainder with a chandelier stop (highest high − TRAIL_ATR × ATR)
      • hard time-stop after MAX_HOLD bars (a stalled EP is a failed EP)

HONEST SCOPE — read this. Bonde is emphatic that a chart with no fundamental
catalyst (earnings, news, guidance) is NOT an EP setup. This repo has no
news/earnings feed and intraday is frozen, so this module captures only the
*technical ignition*, not the catalyst that should gate it. It is therefore the
handicapped, catalyst-blind proxy — pre-registered as such in SPEC_episodic_pivot.md
and expected to underperform a true catalyst-gated EP. Parameters are FIXED (do
not tune to results). Pure logic on one symbol's OHLCV — no I/O, no costs, no
orders.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── Pre-registered parameters (do not tune to results) ────────────────────────
VOL_LOOKBACK = 50      # trailing days for the average-volume baseline
RVOL_MULT    = 2.5     # ignition volume >= this × the trailing average
THRUST_MIN   = 0.05    # ignition day close-to-close move >= +5%
HIGH_LOOKBACK = 60     # ignition close must be the highest of this many days
ATR_N        = 14
TP1          = 0.15    # take half off at +15% (magnitude-move profit-take)
TRAIL_ATR    = 3.0     # chandelier trail on the remainder, in ATR
MAX_HOLD     = 60      # force-exit a stalled EP after this many bars

WARMUP = max(VOL_LOOKBACK, HIGH_LOOKBACK, ATR_N) + 2


def atr(df: pd.DataFrame, n: int = ATR_N) -> np.ndarray:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n, min_periods=1).mean().values


def ignition_mask(df: pd.DataFrame) -> np.ndarray:
    """Boolean array: True on bars where an EP ignition fires (usable at that bar
    with no look-ahead — every input uses data through the bar itself)."""
    c = df["close"].values
    v = df["volume"].values if "volume" in df.columns else np.zeros(len(df))
    n = len(c)
    out = np.zeros(n, dtype=bool)
    if n < WARMUP:
        return out
    close = pd.Series(c)
    vol = pd.Series(v)
    avg_vol_prior = vol.rolling(VOL_LOOKBACK).mean().shift(1).values     # prior window
    prior_close = close.shift(1).values
    high_prior = close.rolling(HIGH_LOOKBACK).max().values              # incl today
    for i in range(WARMUP, n):
        if not (avg_vol_prior[i] > 0):
            continue
        rvol_ok = v[i] >= RVOL_MULT * avg_vol_prior[i]
        thrust_ok = prior_close[i] > 0 and (c[i] / prior_close[i] - 1.0) >= THRUST_MIN
        newhigh_ok = c[i] >= high_prior[i] - 1e-9                        # fresh breakout
        if rvol_ok and thrust_ok and newhigh_ok:
            out[i] = True
    return out


def generate_trades(df: pd.DataFrame) -> list:
    """Walk one symbol's daily OHLCV and return completed EP trades (long only).
    Each trade dict: ignite_date, entry_date, exit_date, side, entry, sl, exit,
    reason, risk (stop distance as a return fraction), gross_ret (blended across
    the scale-out, before costs). One position at a time — a focused EP book."""
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    if len(df) < WARMUP + 2:
        return []
    o, h, l, c = (df["open"].values, df["high"].values,
                  df["low"].values, df["close"].values)
    dates = df.index
    a = atr(df)
    ign = ignition_mask(df)
    n = len(c)

    trades: list = []
    i = WARMUP
    while i < n - 1:
        if not ign[i]:
            i += 1
            continue

        ei = i + 1                              # enter next day's open (no look-ahead)
        entry = o[ei]
        stop = l[i]                             # ignition bar low
        if not (entry > stop > 0):              # need positive, well-ordered risk
            i += 1
            continue
        risk = (entry - stop) / entry
        tp1_price = entry * (1.0 + TP1)

        hh = entry
        half_booked = False
        booked_ret = 0.0                        # realised return from the half sold
        reason = "time"
        exit_price = c[min(ei + MAX_HOLD, n - 1)]
        exit_j = min(ei + MAX_HOLD, n - 1)

        for j in range(ei, min(ei + MAX_HOLD + 1, n)):
            hh = max(hh, h[j])
            # trail the remainder; after TP1 the floor is at least breakeven
            eff_stop = max(stop, hh - TRAIL_ATR * a[j])
            if half_booked:
                eff_stop = max(eff_stop, entry)

            # take half into the first spike (checked on the high)
            if (not half_booked) and h[j] >= tp1_price:
                booked_ret = TP1                # half sold at +TP1
                half_booked = True
                # a bar can both hit TP1 and later trail out; continue managing rest

            # stop / trail exit (checked on the low, conservative)
            if l[j] <= eff_stop:
                exit_price = eff_stop
                exit_j = j
                reason = "trail" if (half_booked or eff_stop > stop) else "stop"
                break
            if j - ei >= MAX_HOLD:
                exit_price = c[j]
                exit_j = j
                reason = "time"
                break

        run_ret = exit_price / entry - 1.0
        gross = (0.5 * booked_ret + 0.5 * run_ret) if half_booked else run_ret
        trades.append({
            "ignite_date": dates[i], "entry_date": dates[ei], "exit_date": dates[exit_j],
            "side": "long", "entry": float(entry), "sl": float(stop),
            "exit": float(exit_price), "reason": reason,
            "risk": float(risk), "gross_ret": float(gross)})
        i = exit_j + 1                          # flat until the trade closes
    return trades
