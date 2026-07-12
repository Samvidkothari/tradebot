"""trailing_exit.py — shared rule-based trailing-stop engine (Pillar 4).

A single, tested "never moves backward" ratcheting exit that any sleeve can use,
generalizing the inline chandelier trail written into episodic_pivot.py. Pure
logic on one position's OHLC — no I/O, no orders, no look-ahead (each bar's stop
uses only information available at that bar's close).

Three composable trailing mechanisms, combined by taking the TIGHTEST (highest
for a long) candidate each bar, then RATCHETED so the stop never loosens:

  • n-bar structural trail  — stop = lowest low of the last N bars (long) minus a
                              buffer; the classic "trail under the n-bar low".
  • ATR chandelier trail    — stop = highest high since entry − mult × ATR.
  • breakeven-after-R       — once price reaches R_TRIGGER multiples of initial
                              risk, the stop can never fall below entry.

The stop is monotonic (non-decreasing for a long, non-increasing for a short) by
construction — the core Pillar-4 invariant. `simulate` walks the bars and returns
the realized exit (bar, price, reason) with the stop checked on the low
(conservative). Longs and shorts symmetric.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TrailConfig:
    """Locked-per-strategy trailing parameters. Any mechanism can be disabled by
    setting its knob to None."""
    n_bar: int | None = 20          # structural trail: lowest-low / highest-high window
    n_bar_buffer_atr: float = 0.0   # extra buffer beyond the n-bar extreme, in ATR
    atr_mult: float | None = 3.0    # chandelier multiple (None disables)
    atr_n: int = 14
    breakeven_R: float | None = 1.0  # lock breakeven after this many R (None disables)


def atr(high, low, close, n: int = 14) -> np.ndarray:
    h, l, c = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(n, min_periods=1).mean().values


def _rolling_extreme(arr, window, kind):
    s = pd.Series(arr)
    r = s.rolling(window, min_periods=1)
    return (r.min() if kind == "min" else r.max()).values


def stop_path(df: pd.DataFrame, entry_idx: int, entry: float, init_stop: float,
              side: str = "long", cfg: TrailConfig = TrailConfig()) -> np.ndarray:
    """Return the ACTIVE stop level governing every bar from `entry_idx` onward.

    Realistic, no-look-ahead mechanic: the stop that can be hit *during* bar i is
    computed from bars strictly before i (you set the stop after a bar closes; it
    is live for subsequent bars). So the entry bar's active stop is `init_stop`,
    and no bar can be stopped out by the very low/high that tightened its own
    trail. The stop starts at `init_stop` and moves only in the favorable
    direction (up for a long, down for a short) — never backward."""
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    a = atr(h, l, c, cfg.atr_n)
    n = len(c)
    risk = abs(entry - init_stop)

    if cfg.n_bar:
        ext = (_rolling_extreme(l, cfg.n_bar, "min") if side == "long"
               else _rolling_extreme(h, cfg.n_bar, "max"))
    hh = -np.inf if side == "long" else np.inf     # running favorable extreme

    active = np.full(n, np.nan)
    comp = init_stop                               # ratcheted stop as of a bar's close
    for i in range(entry_idx, n):
        # the stop live DURING bar i is the one computed through bar i-1
        active[i] = init_stop if i == entry_idx else comp
        # now fold bar i into the ratchet (governs bar i+1 onward)
        if side == "long":
            hh = max(hh, h[i])
            cands = [comp]
            if cfg.n_bar:
                cands.append(ext[i] - cfg.n_bar_buffer_atr * a[i])
            if cfg.atr_mult is not None:
                cands.append(hh - cfg.atr_mult * a[i])
            if cfg.breakeven_R is not None and risk > 0 and \
                    (hh - entry) >= cfg.breakeven_R * risk:
                cands.append(entry)
            comp = max(comp, min(c[i], max(cands)))          # never above close
        else:
            hh = min(hh, l[i])
            cands = [comp]
            if cfg.n_bar:
                cands.append(ext[i] + cfg.n_bar_buffer_atr * a[i])
            if cfg.atr_mult is not None:
                cands.append(hh + cfg.atr_mult * a[i])
            if cfg.breakeven_R is not None and risk > 0 and \
                    (entry - hh) >= cfg.breakeven_R * risk:
                cands.append(entry)
            comp = min(comp, max(c[i], min(cands)))          # never below close
    return active


def simulate(df: pd.DataFrame, entry_idx: int, entry: float, init_stop: float,
             side: str = "long", cfg: TrailConfig = TrailConfig(),
             max_hold: int | None = None) -> dict:
    """Walk bars from entry and exit when the (ratcheted) stop is hit — checked on
    the low for a long, the high for a short (conservative) — or at max_hold.

    Returns {exit_idx, exit_price, reason, stop_path, gross_ret, R}."""
    path = stop_path(df, entry_idx, entry, init_stop, side, cfg)
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    n = len(c)
    risk = abs(entry - init_stop)
    for i in range(entry_idx, n):
        s = path[i]
        hit = (l[i] <= s) if side == "long" else (h[i] >= s)
        if hit:
            ex = s
            reason = "trail" if s != init_stop else "stop"
            gross = (ex / entry - 1) if side == "long" else (entry / ex - 1)
            return {"exit_idx": i, "exit_price": float(ex), "reason": reason,
                    "stop_path": path, "gross_ret": float(gross),
                    "R": float(gross * entry / risk) if risk > 0 else 0.0}
        if max_hold is not None and i - entry_idx >= max_hold:
            ex = c[i]
            gross = (ex / entry - 1) if side == "long" else (entry / ex - 1)
            return {"exit_idx": i, "exit_price": float(ex), "reason": "time",
                    "stop_path": path, "gross_ret": float(gross),
                    "R": float(gross * entry / risk) if risk > 0 else 0.0}
    ex = c[-1]
    gross = (ex / entry - 1) if side == "long" else (entry / ex - 1)
    return {"exit_idx": n - 1, "exit_price": float(ex), "reason": "eod",
            "stop_path": path, "gross_ret": float(gross),
            "R": float(gross * entry / risk) if risk > 0 else 0.0}
