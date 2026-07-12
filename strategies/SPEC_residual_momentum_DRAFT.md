# SPEC — Residual (Idiosyncratic) Momentum  *(DRAFT)*

**Pre-registered 2026-07-09, BEFORE any residual-momentum backtest was run.** No
parameter in this file may change after seeing results (Phase 2B Rule 3). One
parameter set per thesis. If it fails the pass criteria, the thesis failed.

Status: **DRAFT → REJECTED** (2026-07-09 gauntlet). Not wired into
`paper_trader.py`, not in the live REGISTRY, does not modify any pre-registered
file. Long-only, daily bars, NIFTY-50 cash. Paper only; no order code.

Origin: Loop-2 cross-sectional scout. Blitz, Huij & Martens (2011),
"Residual Momentum." Full argument in `THESIS_residual_momentum_DRAFT.md`.

## 1. Universe & data
NIFTY-50 cash names (`data/*.csv`, daily OHLCV via `data_io.load_panel`),
survivorship-biased (current members), 2021-07 → present. Index `NIFTY50.csv` is
the market factor, excluded from the tradable set. Long only, no leverage.

## 2. Locked parameters

| Parameter | Value | Meaning |
|---|---|---|
| `BETA_WIN` | **120** | rolling OLS window for each name's beta vs NIFTY (daily returns) |
| `LOOKBACK` | **252** | momentum formation window (trading days) |
| `SKIP` | **21** | skip most-recent month (12-1 convention) |
| `TOP_N` | **10** | hold the 10 highest residual-momentum names, equal-weight |
| `WARMUP` | **252** | no rebalance until ≥252 bars of history exist |
| rebalance | **first trading day of each calendar month** | |
| valuation | **ffill'd closes** mark-to-market; **raw closes** for eligibility | a name with a gap on the rebalance day is not rankable |

## 3. Mechanical rules (objective, no look-ahead)
1. Daily returns `r_i` (stock), `m` (NIFTY). Rolling `beta_i = cov(r_i,m)/var(m)`
   over `BETA_WIN`, computed through day *t* only.
2. Residual daily return `u_i = r_i − beta_i · m`.
3. Residual momentum score at rebalance day *t* = `sum(u_i over [t−LOOKBACK,
   t−SKIP])` (i.e. rolling-sum of residuals, shifted by `SKIP`).
4. Rank cross-sectionally; go long the `TOP_N` highest, equal-weight; hold to next
   monthly rebalance (buy-and-hold drift between rebalances).
5. Fail-safe: fewer than `TOP_N` rankable names on a date → skip that rebalance.

## 4. Costs
Turnover-aware, identical to the pre-registered backtests:
`bought_value·COST_ENTRY + sold_value·COST_EXIT` (`config.py`; round trip ≈0.32%).
Stress leg re-runs everything at **1.5×** those constants.

## 5. Pass criteria (after costs; default reject)
PASS only if ALL hold:
1. Beat NIFTY-50 buy-and-hold CAGR in **FULL and** OOS (Period B, ≥`SPLIT_DATE`).
2. Max drawdown **no worse than** NIFTY's (FULL period).
3. **OOS Sharpe ≥ 0.95** (multiple-testing haircut = 0.8 + 0.05 × 3 prior ideas
   this quarter — varma_riskstate, episodic_pivot, momentum_governed — computed
   and locked before testing).
4. Survives the **1.5× cost stress** (criteria 1–3 still hold).
Any failure = REJECT.

## 6. Result (2026-07-09, locked run)
**REJECTED.** OOS CAGR 12.8% (beats index 3.9%) ✓ crit-1; but max DD −24.9% vs
index −15.8% ✗ crit-2; OOS Sharpe 0.73 (0.71 @1.5×) < 0.95 ✗ crit-3; walk-forward
OOS CAGR `[0.89, −0.23, 0.32, −0.09]` (2/4 negative, unstable). Momentum-family
decay, not a new edge. No re-tuning.
