"""
strategy.py — SMA crossover signal generation.
Kept separate so the backtest and the Phase 3 live loop share the
exact same signal logic without duplicating it.
"""

import pandas as pd

# ── Strategy parameters ───────────────────────────────────────────────────────
FAST_PERIOD = 20   # days
SLOW_PERIOD = 50   # days
# ─────────────────────────────────────────────────────────────────────────────


def generate_signals(df, fast=FAST_PERIOD, slow=SLOW_PERIOD):
    """
    Moving average crossover on daily close prices.

    Returns a copy of df with three extra columns:
      sma_fast  — rolling mean over `fast` days
      sma_slow  — rolling mean over `slow` days
      position  — 1 = long, 0 = flat

    Signal rule:
      position = 1 when sma_fast > sma_slow (golden cross region)
      position = 0 when sma_fast < sma_slow (death cross region)
      position = 0 for the first `slow` rows (SMAs not yet valid)

    Execution is NOT handled here — the backtest engine decides
    when and how to fill (next-day open, next-day close, etc.).
    """
    df = df.copy()
    df["sma_fast"] = df["close"].rolling(fast).mean()
    df["sma_slow"] = df["close"].rolling(slow).mean()

    both_valid = df["sma_fast"].notna() & df["sma_slow"].notna()
    df["position"] = 0
    df.loc[both_valid & (df["sma_fast"] > df["sma_slow"]), "position"] = 1

    return df


def generate_signal(df, fast=FAST_PERIOD, slow=SLOW_PERIOD):
    """
    Reduce the full signal series to ONE decision for the latest bar.
    This is the clean interface the Phase 3 live loop calls each day.

    Returns one of:
      "BUY"  — strategy wants to be LONG  (fast SMA above slow SMA)
      "SELL" — strategy wants to be FLAT  (fast SMA below slow SMA)
      "HOLD" — not enough history yet to decide (SMA warmup period)

    This is a TARGET-STATE signal, not a one-day event. The caller
    compares it against what it currently holds and acts only on the
    difference:
        BUY  while flat  -> enter
        SELL while long  -> exit
        otherwise        -> do nothing
    Designed this way so a once-daily script stays correct even if you
    skip a day — it always reconciles to the right target state rather
    than relying on catching the exact crossover day.
    """
    d = generate_signals(df, fast, slow)
    if d.empty:
        return "HOLD"

    last = d.iloc[-1]
    if pd.isna(last["sma_fast"]) or pd.isna(last["sma_slow"]):
        return "HOLD"

    return "BUY" if last["position"] == 1 else "SELL"
