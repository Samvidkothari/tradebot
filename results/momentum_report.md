# Cross-Sectional Momentum (12-1) — Backtest Report

Generated: 2026-06-29  
Spec: `strategies/SPEC_momentum.md` (pre-registered, commit cd1b698).  
Strategy: monthly rebalance into the top 15 NIFTY 50 names by 12-1 momentum (skip most-recent month), equal-weight.  
Universe: 48 stocks (survivorship-biased — current members only; see spec §2).  
Lookback 273d (formation 252 + skip 21); round-trip cost ≈0.323%, charged on turnover.  

## Full Period

| Metric | Momentum | NIFTY 50 B&H |
|---|---|---|
| CAGR | +14.7% | +8.8% |
| Total return | +70.6% | +38.7% |
| Max drawdown | -27.2% | -15.8% |
| Years | 3.9 | 3.9 |

## Period A — before 2024-01 (in-sample proxy)

| Metric | Momentum | NIFTY 50 B&H |
|---|---|---|
| CAGR | +30.5% | +17.4% |
| Total return | +45.6% | +25.3% |
| Max drawdown | -17.0% | -9.9% |
| Years | 1.4 | 1.4 |

## Period B — from 2024-01 (out-of-sample)

| Metric | Momentum | NIFTY 50 B&H |
|---|---|---|
| CAGR | +6.6% | +4.2% |
| Total return | +17.2% | +10.6% |
| Max drawdown | -27.2% | -15.8% |
| Years | 2.5 | 2.5 |

## Pre-Committed PASS Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Beat NIFTY CAGR in FULL **and** Period B | PASS |
| 2 | Max drawdown no worse than NIFTY's | FAIL |
| 3 | >= 30 position-changes (got 269) | PASS |

**VERDICT: FAIL**

## Honest Assessment

- **Full period:** momentum +14.7% CAGR vs NIFTY +8.8% — beat buy-and-hold after all costs.
- **Out-of-sample (Period B):** momentum +6.6% vs NIFTY +4.2% — held up.
- **Drawdown:** worst momentum drawdown -27.2% vs NIFTY -15.8%.
- **Activity:** 269 position-changes across 47 monthly rebalances.
- **Survivorship bias:** today's NIFTY 50 membership applied to the past; a true point-in-time test would likely be somewhat worse (spec §2).
- **Standing benchmark:** NIFTY 50 buy-and-hold delivers ≈+8% CAGR with roughly half the drawdown for zero effort. That is the bar.
- **Phase 2B accounting:** this is one of the two permitted strategy-class attempts. One attempt remains.
