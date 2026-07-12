# SPEC — Momentum governed by the Varma risk-state sizer

**Pre-registered 2026-07-08.** Pass criteria were committed in
`backtest_momentum_governed.evaluate()` BEFORE the run. The momentum signal and
the Varma sizer parameters are both already locked; nothing here is tuned to this
result. One parameter set per thesis.

Status: **CANDIDATE**. Not wired into `paper_trader.py`. Long-only, NIFTY-50,
monthly. Paper only; no order code.

## What it is

The pre-registered cross-sectional **12-1 momentum selection**
(`momentum.target_portfolio`, unchanged) with the graded, fractional-Kelly
**`varma_riskstate` exposure factor** applied to gross exposure each rebalance.
Freed capital (1 − factor) sits in cash. This is the blueprint's highest-ROI fix:
momentum is a real edge whose only flaw is a deep drawdown, which is a *sizing*
problem, not a *signal* problem.

## What it is NOT

- It does **not** change the momentum signal, universe, cadence, TOP_N, or cost
  model; `momentum.py` and `backtest_momentum.py` are untouched and their
  committed verdict stands. The ungoverned baseline in this backtest IS the
  canonical `run_momentum`.
- It **never increases** exposure: factor ≤ 1.00 (verified by construction and test).

## Rules

- Selection: `target_portfolio(panel, date, top_n=15)` — pre-registered, unchanged.
- Sizing: each rebalance, `exposure = varma_riskstate.exposure_factor(NIFTY≤date).factor`
  (∈ [0.40, 1.00]); invest `exposure × equity` equal-weight across 15, rest cash.
- Costs: same turnover-aware model (`COST_ENTRY/EXIT`) as the canonical backtest.
- OOS split 2024-01-01; benchmark NIFTY-50 buy-and-hold.

## Pass criteria (judged on the governed book, after costs)

PASS only if ALL hold:
1. **Governed max drawdown magnitude < ungoverned** (the governor's whole purpose).
2. **Better Calmar (CAGR / |MaxDD|)** than ungoverned in the **full period AND
   out-of-sample** (risk-adjusted improvement that survives OOS).
3. **Retains ≥ 60% of ungoverned CAGR** (the edge is not gutted).

Anything else = FAIL. Default reject; human-only promotion.

## Result (2026-07-08, full history to date — honest read)

| Book (full) | CAGR | Max DD | Calmar | Sharpe |
|---|---|---|---|---|
| Ungoverned momentum | +13.3% | −27.2% | 0.49 | 0.84 |
| **Governed momentum** | **+10.7%** | **−21.2%** | **0.50** | **0.85** |
| NIFTY-50 B&H | +8.3% | −15.8% | 0.53 | — |

Verdict: **PASS**. The governor cut the worst drawdown by ~6 points (−27.2% →
−21.2%) while keeping ~80% of the CAGR. **Honest caveat:** the risk-adjusted gain
is *marginal* (Calmar +0.01, Sharpe +0.01 full period) and **out-of-sample it is
razor-thin** (Calmar essentially flat, Sharpe fractionally lower). So the durable,
robust effect is **drawdown reduction**, not a return improvement. Treat as a mild,
promising win that needs a forward shadow period before promotion — not a
breakthrough. Both remain long NIFTY names, so this sleeve is not a diversifier;
its value is a smaller left tail on an existing edge.
