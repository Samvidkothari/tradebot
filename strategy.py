"""
strategy.py — SMA crossover signal generation.
Kept separate so the backtest and the Phase 3 live loop share the
exact same signal logic without duplicating it.
"""

import pandas as pd

# ── Strategy parameters ───────────────────────────────────────────────────────
FAST_PERIOD  = 20    # days — short moving average
SLOW_PERIOD  = 50    # days — long moving average (the crossover signal)
TREND_PERIOD = 200   # days — regime filter; only go long when close is above this
# Set TREND_PERIOD = None to disable the trend filter (plain 20/50 crossover).
# ─────────────────────────────────────────────────────────────────────────────


def generate_signals(df, fast=FAST_PERIOD, slow=SLOW_PERIOD, trend=TREND_PERIOD):
    """
    Moving average crossover with an optional long-term trend filter.

    Returns a copy of df with these extra columns:
      sma_fast   — rolling mean over `fast` days
      sma_slow   — rolling mean over `slow` days
      sma_trend  — rolling mean over `trend` days (only if trend is set)
      position   — 1 = long, 0 = flat

    Signal rule (go long only when BOTH are true):
      • sma_fast > sma_slow          (golden-cross region — short-term up)
      • close    > sma_trend         (price above long-term trend — regime up)
    Otherwise flat. The trend filter keeps us out of stocks that are in a
    long-term downtrend even if a short-term crossover fires — the whole
    point of adding it. Rows before the SMAs are valid are flat.

    Execution is NOT handled here — the backtest engine decides
    when and how to fill (next-day open, next-day close, etc.).
    """
    df = df.copy()
    df["sma_fast"] = df["close"].rolling(fast).mean()
    df["sma_slow"] = df["close"].rolling(slow).mean()

    valid     = df["sma_fast"].notna() & df["sma_slow"].notna()
    long_cond = df["sma_fast"] > df["sma_slow"]

    if trend:
        df["sma_trend"] = df["close"].rolling(trend).mean()
        valid     = valid & df["sma_trend"].notna()
        long_cond = long_cond & (df["close"] > df["sma_trend"])

    df["position"] = 0
    df.loc[valid & long_cond, "position"] = 1

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
    warmup = pd.isna(last["sma_fast"]) or pd.isna(last["sma_slow"])
    if "sma_trend" in d.columns:
        warmup = warmup or pd.isna(last["sma_trend"])
    if warmup:
        return "HOLD"

    return "BUY" if last["position"] == 1 else "SELL"
