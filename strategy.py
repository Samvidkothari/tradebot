"""
strategy.py — SMA crossover signal generation.
Kept separate so Phase 3 can import generate_signals() directly
into a live signal loop without pulling in any backtest code.
"""

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
