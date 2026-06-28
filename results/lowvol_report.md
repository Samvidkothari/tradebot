# Low-Volatility Anomaly (60d) — Backtest Report

Generated: 2026-06-28  
Spec: `strategies/SPEC_lowvol.md` (pre-registered, commit 664b492).  
Strategy: monthly rebalance into the 15 LOWEST-volatility NIFTY 50 names by 60-day realized vol, equal-weight.  
Universe: 48 stocks (survivorship-biased — current members only; see spec §2).  
Warmup 61d (60 returns); round-trip cost ≈0.323%, charged on turnover.  

## Full Period

| Metric | Low-Vol | NIFTY 50 B&H |
|---|---|---|
| CAGR | +11.1% | +6.9% |
| Total return | +64.9% | +37.2% |
| Max drawdown | -15.9% | -17.2% |
| Years | 4.7 | 4.7 |

## Period A — before 2024-01 (in-sample proxy)

| Metric | Low-Vol | NIFTY 50 B&H |
|---|---|---|
| CAGR | +15.2% | +10.0% |
| Total return | +37.3% | +24.0% |
| Max drawdown | -14.4% | -17.2% |
| Years | 2.2 | 2.2 |

## Period B — from 2024-01 (out-of-sample)

| Metric | Low-Vol | NIFTY 50 B&H |
|---|---|---|
| CAGR | +7.6% | +4.2% |
| Total return | +20.0% | +10.6% |
| Max drawdown | -15.9% | -15.8% |
| Years | 2.5 | 2.5 |

## Pre-Committed PASS Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Beat NIFTY CAGR in FULL **and** Period B | PASS |
| 2 | Max drawdown no worse than NIFTY's | PASS |
| 3 | >= 30 position-changes (got 431) | PASS |

**VERDICT: PASS**

## Honest Assessment

- **Full period:** low-vol +11.1% CAGR vs NIFTY +6.9% — beat buy-and-hold after all costs.
- **Out-of-sample (Period B):** low-vol +7.6% vs NIFTY +4.2% — held up.
- **Drawdown:** worst low-vol drawdown -15.9% vs NIFTY -17.2% — this is the criterion the thesis was built to win, since it holds the calmest names.
- **Activity:** 431 position-changes across 57 monthly rebalances (low-vol turnover is expected to be modest).
- **Survivorship bias:** today's NIFTY 50 membership applied to the past; a true point-in-time test would likely be somewhat worse (spec §2).
- **Standing benchmark:** NIFTY 50 buy-and-hold delivers ≈+8% CAGR with roughly half the drawdown for zero effort. That is the bar.
- **Phase 2B accounting:** this strategy PASSED — Phase 3 paper trading may proceed on it (spec §8).
