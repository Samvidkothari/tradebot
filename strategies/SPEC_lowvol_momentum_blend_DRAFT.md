# SPEC — Low-Vol × Momentum Rank Blend  *(DRAFT)*

**Pre-registered 2026-07-12, BEFORE any blend backtest was run.** No parameter in
this file may change after seeing results (Phase 2B Rule 3). One parameter set
per thesis. If it fails the pass criteria, the thesis failed.

Status: **DRAFT → REJECTED** (2026-07-12 gauntlet). Not wired into
`paper_trader.py`, not in the live REGISTRY, does not modify any pre-registered
file. Long-only, daily bars, NIFTY-50 cash. Paper only; no order code.

Origin: Loop-2 cross-sectional scout, 2026-07-12. Full argument in
`THESIS_lowvol_momentum_blend_DRAFT.md`.

## 1. Universe & data
NIFTY-50 cash names (`data/*.csv`, daily OHLCV via `data_io.load_panel`),
survivorship-biased (current members). `NIFTY50.csv` is the benchmark only.
Long only, no leverage, no shorting, no derivatives → no unbounded tail.

## 2. Locked parameters

| Parameter | Value | Meaning |
|---|---|---|
| `VOL_LOOKBACK` | **60** | realized-vol window (identical to SPEC_lowvol) |
| `FORMATION` / `SKIP` | **252 / 21** | 12-1 momentum convention (identical to SPEC_momentum) |
| blend | **0.5 / 0.5** | equal weight of the two percentile ranks |
| `TOP_N` | **15** | hold the 15 best blended names, equal-weight |
| `WARMUP` | **273** | no rebalance until both signals are formable |
| rebalance | **first trading day of each calendar month** | |
| valuation | ffill'd closes to mark; **raw closes** for ranking | as in the pre-registered backtests |

## 3. Mechanical rules (objective, no look-ahead)
1. At each rebalance day *t* (integer pos ≥ `WARMUP`): compute `lowvol.vol_scores`
   and `momentum.momentum_scores` at pos *t* (both signal modules used unchanged,
   read-only).
2. Eligible set = names rankable in **both** sorts.
3. `vol_score` = 1 − pct_rank(vol) (lower vol → higher score);
   `mom_score` = pct_rank(momentum) (higher momentum → higher score).
   `blend = 0.5·vol_score + 0.5·mom_score`.
4. Hold the `TOP_N` highest blend scores, equal-weight; buy-and-hold drift to the
   next monthly rebalance.
5. Fail-safe: fewer than `TOP_N` eligible names → skip that rebalance (hold).

## 4. Costs
Turnover-aware, identical to the pre-registered backtests:
`bought_value·COST_ENTRY + sold_value·COST_EXIT` (`config.py`; round trip
≈0.32%), two-pass so cash is never overdrawn. Stress leg re-runs at **1.5×**.

## 5. Pass criteria (after costs; default reject)
PASS only if ALL hold:
1. Beat NIFTY-50 buy-and-hold CAGR in **FULL and** OOS (≥ `config.SPLIT_DATE`).
2. Max drawdown **no worse than** NIFTY's (FULL period).
3. **OOS Sharpe ≥ 1.05** (multiple-testing haircut = 0.8 + 0.05 × 5 prior ideas
   this quarter — varma_riskstate, episodic_pivot, momentum_governed,
   residual_momentum, turn_of_month — computed and locked before testing).
4. Survives the **1.5× cost stress** (criteria 1–3 still hold).
Any failure = REJECT. A PASS additionally faces the Risk-Manager correlation
check (>0.8 60d correlation to the live low-vol sleeve → reject as redundant)
before it can be marked CANDIDATE.

## 6. Result (2026-07-12, locked run)
**REJECTED.** FULL: CAGR 13.8%, Sharpe 1.11, maxDD −17.6%. OOS: CAGR 8.1%
(beats index 3.9% ✓ crit-1) but Sharpe 0.64 (0.60 @1.5×) < 1.05 ✗ crit-3, and
FULL maxDD −17.6% vs index −15.8% ✗ crit-2 — the low-vol conditioning did not
neutralize momentum's tail. Walk-forward CAGR `[0.13, 0.56, 0.03, −0.07]`,
fading into the latest segment: the momentum-family decay signature again.
Footnote: it did beat the un-gated low-vol replica on OOS CAGR (8.1% vs 7.3%)
at identical Sharpe — a small return interaction, no risk-adjusted edge.
No re-tuning.
