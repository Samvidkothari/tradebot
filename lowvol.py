"""
lowvol.py — Low-volatility anomaly signal logic.
Implements exactly the rules pre-registered in strategies/SPEC_lowvol.md.

This is the reusable, live-compatible interface:
    target_portfolio(panel, date) -> [symbols]   # the names to hold
The Phase 3 paper adapter diffs this target against current holdings and
emits BUY/SELL actions — the same "compute target -> diff -> act" pattern.
"""

import pandas as pd

# ── Parameters (pre-registered — do not tune to results) ──────────────────────
VOL_LOOKBACK = 60          # trading days (~3 months) of daily returns
WARMUP       = VOL_LOOKBACK + 1   # 61 closes needed to form 60 returns
TOP_N        = 15          # hold the 15 LOWEST-vol names, equal-weight
MAX_WEIGHT   = 1 / TOP_N
# ─────────────────────────────────────────────────────────────────────────────


def vol_scores(panel, pos):
    """
    Realized-volatility score for every symbol at integer index `pos` of `panel`.

        vol = std of the last VOL_LOOKBACK daily simple returns ending at `pos`
        ret[i] = close[i] / close[i-1] - 1

    A symbol is rankable only if it has VOL_LOOKBACK+1 = 61 valid CONSECUTIVE
    closes in panel.iloc[pos-VOL_LOOKBACK .. pos] (no NaN gaps). Lower vol = more
    desirable. `panel` is a DataFrame of daily closes (index=dates, cols=symbols).
    Returns a Series of vols, rankable symbols only, sorted LOW -> high. Empty
    during warmup.
    """
    if pos < VOL_LOOKBACK:
        return pd.Series(dtype=float)

    window = panel.iloc[pos - VOL_LOOKBACK: pos + 1]   # 61 rows
    rankable = window.notna().all()                    # all 61 closes present
    win = window.loc[:, rankable]
    if win.empty:
        return pd.Series(dtype=float)

    rets = win.pct_change().iloc[1:]                   # 60 daily simple returns
    vol  = rets.std()                                  # sample std per symbol
    return vol.sort_values(ascending=True)


def target_portfolio(panel, date, top_n=TOP_N):
    """Return the symbols to hold as of `date` (the top_n LOWEST-vol names)."""
    pos = panel.index.get_loc(date)
    scores = vol_scores(panel, pos)
    return list(scores.index[:top_n])
