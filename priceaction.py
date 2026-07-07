"""priceaction.py — mechanical price-action swing strategy (signal logic).

Encodes the popular 3-step "price action" method as OBJECTIVE, no-look-ahead
rules so it can be backtested honestly instead of eyeballed:

  1. Market structure — 5-bar fractal swings, confirmed L bars late; trend flips
     on a break of the last CONFIRMED swing (break-of-structure).
  2. Supply/demand zone — a tight base (>=BASE_MIN bars, each range <= BASE_ATR*
     ATR) immediately followed by an impulse (> IMP_ATR*ATR) in the trend
     direction. Demand below (up-impulse), supply above (down-impulse).
  3. Risk-reward filter — enter on a retrace into the zone only if
     (target-entry)/(entry-stop) >= RR_MIN. Long demand in uptrends, short
     supply in downtrends.

Pure logic on one symbol's OHLC — no I/O, no costs, no orders (the backtest adds
costs and aggregates). Pre-registered in strategies/SPEC_priceaction.md; the
parameters below are FIXED (do not tune to results).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── Pre-registered parameters (do not tune to results) ────────────────────────
L        = 2      # fractal half-window → 5-bar swing pivots
ATR_N    = 14
BASE_MIN = 3      # bars in a base
BASE_ATR = 0.8    # each base bar's range <= BASE_ATR * ATR
IMP_ATR  = 1.5    # impulse move > IMP_ATR * ATR
SL_BUF   = 0.5    # stop buffer beyond the zone, in ATR
RR_MIN   = 2.5    # minimum reward:risk to take a trade
ZONE_TTL = 40     # a zone expires after this many bars if untouched
MAX_HOLD = 40     # force-exit a trade after this many bars

WARMUP = ATR_N + 2 * L + BASE_MIN + 5


def atr(df: pd.DataFrame, n: int = ATR_N) -> np.ndarray:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n, min_periods=1).mean().values


def _confirmed_swings(h: np.ndarray, l: np.ndarray, L: int):
    """sh[i]/sl[i] = price of a swing high/low that becomes CONFIRMED (usable) at
    bar i — i.e. the pivot sat at i-L and has L bars either side. NaN elsewhere.
    Using it only at/after i means no look-ahead."""
    n = len(h)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    for p in range(L, n - L):
        if h[p] == np.max(h[p - L:p + L + 1]):
            sh[p + L] = h[p]
        if l[p] == np.min(l[p - L:p + L + 1]):
            sl[p + L] = l[p]
    return sh, sl


def generate_trades(df: pd.DataFrame) -> list:
    """Walk one symbol's daily OHLC and return a list of completed trades. Each
    trade is a dict: entry_date, exit_date, side, entry, sl, tp, exit, reason,
    risk (stop distance as a return fraction), gross_ret (before costs)."""
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    if len(df) < WARMUP:
        return []
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    dates = df.index
    a = atr(df)
    sh, sl_ = _confirmed_swings(h, l, L)
    n = len(c)

    last_sh = np.nan   # most recent CONFIRMED swing high / low prices
    last_sl = np.nan
    trend = "none"
    zones: list = []   # {kind, lo, hi, created, used}
    trades: list = []
    pos = None         # open trade dict

    for i in range(1, n):
        if not np.isnan(sh[i]):
            last_sh = sh[i]
        if not np.isnan(sl_[i]):
            last_sl = sl_[i]
        # break-of-structure trend
        if not np.isnan(last_sh) and c[i] > last_sh:
            trend = "up"
        if not np.isnan(last_sl) and c[i] < last_sl:
            trend = "down"

        # new zone: impulse at bar i preceded by a tight base
        if a[i] > 0 and i - BASE_MIN >= 1:
            move = c[i] - c[i - 1]
            if abs(move) > IMP_ATR * a[i]:
                rng = h[i - BASE_MIN:i] - l[i - BASE_MIN:i]
                if rng.size and np.all(rng <= BASE_ATR * a[i]):
                    lo = float(np.min(l[i - BASE_MIN:i]))
                    hi = float(np.max(h[i - BASE_MIN:i]))
                    zones.append({"kind": "demand" if move > 0 else "supply",
                                  "lo": lo, "hi": hi, "created": i, "used": False})
        zones = [z for z in zones if i - z["created"] <= ZONE_TTL and not z["used"]]

        # manage an open position
        if pos is not None:
            hit = None
            if pos["side"] == "long":
                if l[i] <= pos["sl"]:
                    hit = ("sl", pos["sl"])
                elif h[i] >= pos["tp"]:
                    hit = ("tp", pos["tp"])
            else:
                if h[i] >= pos["sl"]:
                    hit = ("sl", pos["sl"])
                elif l[i] <= pos["tp"]:
                    hit = ("tp", pos["tp"])
            if hit is None and i - pos["ei"] >= MAX_HOLD:
                hit = ("time", c[i])
            if hit:
                ex = hit[1]
                gross = (ex / pos["entry"] - 1) if pos["side"] == "long" \
                    else (pos["entry"] / ex - 1)
                trades.append({"entry_date": dates[pos["ei"]], "exit_date": dates[i],
                               "side": pos["side"], "entry": pos["entry"], "sl": pos["sl"],
                               "tp": pos["tp"], "exit": ex, "reason": hit[0],
                               "risk": pos["risk"], "gross_ret": gross})
                pos = None
            continue

        # entry when flat: retrace into a fresh zone in the trend direction
        for z in zones:
            if z["used"]:
                continue
            if trend == "up" and z["kind"] == "demand" and not np.isnan(last_sh):
                entry = z["hi"]
                stop = z["lo"] - SL_BUF * a[i]
                target = last_sh
                if l[i] <= z["hi"] and l[i] >= stop and target > entry > stop:
                    if (target - entry) / (entry - stop) >= RR_MIN:
                        pos = {"side": "long", "ei": i, "entry": entry, "sl": stop,
                               "tp": target, "risk": (entry - stop) / entry}
                        z["used"] = True
                        break
            elif trend == "down" and z["kind"] == "supply" and not np.isnan(last_sl):
                entry = z["lo"]
                stop = z["hi"] + SL_BUF * a[i]
                target = last_sl
                if h[i] >= z["lo"] and h[i] <= stop and stop > entry > target:
                    if (entry - target) / (stop - entry) >= RR_MIN:
                        pos = {"side": "short", "ei": i, "entry": entry, "sl": stop,
                               "tp": target, "risk": (stop - entry) / entry}
                        z["used"] = True
                        break
    return trades
