"""
momentum.py — Cross-sectional momentum signal logic.
Implements exactly the rules pre-registered in strategies/SPEC_momentum.md.

This is the reusable, live-compatible interface:
    target_portfolio(panel, date) -> [symbols]   # the names to hold
The Phase 3 paper adapter diffs this target against current holdings and
emits BUY/SELL actions — the same "compute target -> diff -> act" pattern.
"""

import pandas as pd

# ── Parameters (pre-registered — do not tune to results) ──────────────────────
FORMATION  = 252   # trading days ≈ 12 months
SKIP       = 21    # trading days ≈ 1 month (skip most-recent month)
LOOKBACK   = FORMATION + SKIP   # 273 — rows of history needed before a score
TOP_N      = 15    # hold the top 15 (~30% of a 50-name universe), equal-weight
MAX_WEIGHT = 1 / TOP_N
# ─────────────────────────────────────────────────────────────────────────────


def momentum_scores(panel, pos):
    """
    12-1 momentum score for every symbol at integer index `pos` of `panel`.

        score = close[pos - SKIP] / close[pos - LOOKBACK] - 1

    `panel` is a DataFrame of daily closes (index = dates, columns = symbols).
    Returns a Series of scores, only for symbols rankable at `pos` (valid close
    at both ends, positive base), sorted high → low. Empty during warmup.
    """
    if pos < LOOKBACK:
        return pd.Series(dtype=float)

    recent = panel.iloc[pos - SKIP]
    base   = panel.iloc[pos - LOOKBACK]

    valid  = recent.notna() & base.notna() & (base > 0)
    score  = (recent[valid] / base[valid]) - 1
    return score.sort_values(ascending=False)


def target_portfolio(panel, date, top_n=TOP_N):
    """Return the list of symbols to hold as of `date` (top_n by momentum)."""
    pos = panel.index.get_loc(date)
    scores = momentum_scores(panel, pos)
    return list(scores.index[:top_n])
