# Low-Volatility Anomaly (60d) — Backtest Report

Generated: 2026-07-14  
Spec: `strategies/SPEC_lowvol.md` (pre-registered, commit 664b492).  
Strategy: monthly rebalance into the 15 LOWEST-volatility NIFTY 50 names by 60-day realized vol, equal-weight.  
Universe: 48 stocks (survivorship-biased — current members only; see spec §2).  
Warmup 61d (60 returns); round-trip cost ≈0.323%, charged on turnover.  

## Full Period

| Metric | Low-Vol | NIFTY 50 B&H |
|---|---|---|
| CAGR | +11.0% | +6.6% |
| Total return | +63.3% | +35.0% |
| Max drawdown | -15.9% | -16.5% |
| Years | 4.7 | 4.7 |

## Period A — before 2024-01 (in-sample proxy)

| Metric | Low-Vol | NIFTY 50 B&H |
|---|---|---|
| CAGR | +15.2% | +9.3% |
| Total return | +35.8% | +21.2% |
| Max drawdown | -14.4% | -16.5% |
| Years | 2.2 | 2.2 |

## Period B — from 2024-01 (out-of-sample)

| Metric | Low-Vol | NIFTY 50 B&H |
|---|---|---|
| CAGR | +7.5% | +4.3% |
| Total return | +20.1% | +11.4% |
| Max drawdown | -15.9% | -15.8% |
| Years | 2.5 | 2.5 |

## Pre-Committed PASS Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | Beat NIFTY CAGR in FULL **and** Period B | PASS |
| 2 | Max drawdown no worse than NIFTY's | PASS |
| 3 | >= 30 position-changes (got 433) | PASS |

**VERDICT: PASS**

## Honest Assessment

- **Full period:** low-vol +11.0% CAGR vs NIFTY +6.6% — beat buy-and-hold after all costs.
- **Out-of-sample (Period B):** low-vol +7.5% vs NIFTY +4.3% — held up.
- **Drawdown:** worst low-vol drawdown -15.9% vs NIFTY -16.5% — this is the criterion the thesis was built to win, since it holds the calmest names.
- **Activity:** 433 position-changes across 57 monthly rebalances (low-vol turnover is expected to be modest).
- **Survivorship bias:** today's NIFTY 50 membership applied to the past; a true point-in-time test would likely be somewhat worse (spec §2).
- **Standing benchmark:** NIFTY 50 buy-and-hold delivers ≈+8% CAGR with roughly half the drawdown for zero effort. That is the bar.
- **Phase 2B accounting:** this strategy PASSED — Phase 3 paper trading may proceed on it (spec §8).
