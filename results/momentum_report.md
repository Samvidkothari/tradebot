# Cross-Sectional Momentum (12-1) — Backtest Report

Generated: 2026-07-14  
Spec: `strategies/SPEC_momentum.md` (pre-registered, commit cd1b698).  
Strategy: monthly rebalance into the top 15 NIFTY 50 names by 12-1 momentum (skip most-recent month), equal-weight.  
Universe: 48 stocks (survivorship-biased — current members only; see spec §2).  
Lookback 273d (formation 252 + skip 21); round-trip cost ≈0.323%, charged on turnover.  

## Full Period

| Metric | Momentum | NIFTY 50 B&H |
|---|---|---|
| CAGR | +13.4% | +8.7% |
| Total return | +62.8% | +38.0% |
| Max drawdown | -27.2% | -15.8% |
| Years | 3.9 | 3.9 |

## Period A — before 2024-01 (in-sample proxy)

| Metric | Momentum | NIFTY 50 B&H |
|---|---|---|
| CAGR | +27.5% | +17.5% |
| Total return | +38.0% | +23.9% |
| Max drawdown | -17.0% | -9.9% |
| Years | 1.3 | 1.3 |

## Period B — from 2024-01 (out-of-sample)

| Metric | Momentum | NIFTY 50 B&H |
|---|---|---|
| CAGR | +6.8% | +4.3% |
| Total return | +18.0% | +11.4% |
| Max drawdown | -27.2% | -15.8% |
| Years | 2.5 | 2.5 |

## Pre-Committed PASS Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Beat NIFTY CAGR in FULL **and** Period B | PASS |
| 2 | Max drawdown no worse than NIFTY's | FAIL |
| 3 | >= 30 position-changes (got 277) | PASS |

**VERDICT: FAIL**

## Honest Assessment

- **Full period:** momentum +13.4% CAGR vs NIFTY +8.7% — beat buy-and-hold after all costs.
- **Out-of-sample (Period B):** momentum +6.8% vs NIFTY +4.3% — held up.
- **Drawdown:** worst momentum drawdown -27.2% vs NIFTY -15.8%.
- **Activity:** 277 position-changes across 47 monthly rebalances.
- **Survivorship bias:** today's NIFTY 50 membership applied to the past; a true point-in-time test would likely be somewhat worse (spec §2).
- **Standing benchmark:** NIFTY 50 buy-and-hold delivers ≈+8% CAGR with roughly half the drawdown for zero effort. That is the bar.
- **Phase 2B accounting:** this is one of the two permitted strategy-class attempts. One attempt remains.
