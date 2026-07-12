# SPEC — Turn-of-Month Index Seasonality  *(DRAFT)*

**Pre-registered 2026-07-09, BEFORE any ToM backtest was run.** No parameter in
this file may change after seeing results (Phase 2B Rule 3). One parameter set per
thesis. If it fails the pass criteria, the thesis failed.

Status: **DRAFT → REJECTED** (2026-07-09 gauntlet). Not wired into
`paper_trader.py`, not in the live REGISTRY, does not modify any pre-registered
file. Defined-risk (long basket or cash — never short, never levered), daily bars,
NIFTY-50 cash. Paper only; no order code.

Origin: Loop-2 vol/expiry-calendar scout. Full argument in
`THESIS_turn_of_month_DRAFT.md`.

## 1. Universe & data
NIFTY-50 cash names (`data/*.csv`, daily OHLCV via `data_io.load_panel`),
equal-weight basket as the risky asset; cash (0% return) as the safe asset.
Benchmark = NIFTY-50 buy-and-hold. 2021-07 → present.

## 2. Locked parameters

| Parameter | Value | Meaning |
|---|---|---|
| `LAST_K` | **1** | number of trailing trading days of the month in the window |
| `FIRST_K` | **3** | number of leading trading days of the next month in the window |
| window | **ToM = [−1, +3]** | long on those days, cash otherwise |
| basket | **equal-weight, all eligible names** | rebalanced only on state change |
| trade points | **state changes only** | one full entry at window start, one full exit at window end |

## 3. Mechanical rules (objective, no look-ahead)
1. For each trading day compute position-from-start and position-from-end within
   its calendar month.
2. Day is **ON** (long the equal-weight basket) if it is within the last `LAST_K`
   trading days OR the first `FIRST_K` trading days of a month; else **OFF** (cash).
3. On an OFF→ON transition, buy the full equal-weight basket of all names with a
   raw close that day. On an ON→OFF transition, sell to cash. No intra-window
   trading (buy-and-hold drift inside the window).
4. Fail-safe: a name without a raw close on entry day is simply excluded from that
   window's basket.

## 4. Costs
Turnover-aware, identical to the pre-registered backtests:
`bought_value·COST_ENTRY + sold_value·COST_EXIT` (`config.py`; round trip ≈0.32%).
Stress leg re-runs at **1.5×**. NOTE: full in/out monthly ⇒ ~12 round trips/yr on
notional ≈ 3.8%/yr modeled drag — this is the crux the premium must beat.

## 5. Pass criteria (after costs; default reject)
PASS only if ALL hold:
1. Beat NIFTY-50 buy-and-hold CAGR in OOS (Period B, ≥`SPLIT_DATE`).
2. Max drawdown **no worse than** NIFTY's (FULL period).
3. **OOS Sharpe ≥ 1.00** (multiple-testing haircut = 0.8 + 0.05 × 4 prior ideas
   this quarter — varma_riskstate, episodic_pivot, momentum_governed,
   residual_momentum — computed and locked before testing).
4. Survives the **1.5× cost stress** (criteria 1–3 still hold).
Any failure = REJECT.

## 6. Result (2026-07-09, locked run)
**REJECTED.** Max DD −10.6% vs index −15.8% ✓ crit-2; but OOS CAGR −0.05% (1×) /
−2.1% (1.5×) fails to beat index 3.9% ✗ crit-1; OOS Sharpe 0.03 (−0.23 @1.5×) <
1.00 ✗ crit-3. Monthly full-basket churn does not survive this cost model —
the settled intraday cost lesson, restated. No re-tuning.
