# SPEC — Intraday / Higher-Frequency COST GATE (Framework pillar B)

**Pre-registered 2026-07-08.** The gate every intraday or higher-frequency
strategy must clear BEFORE any live-execution code is written. Parameters locked
in `cost_gate.py`; they may not be loosened to let an idea through. Paper/research
gate; no orders.

## Why this exists

The intraday book was frozen on hard evidence: a real **gross +₹11,405** edge
became **net −₹21,509** after costs (`FUND_BLUEPRINT_2026-07-06.md`). Reopening
intraday ("pillar B") is allowed **only** if the operator's rule is honored:
*B only if it clears the cost gate.* This SPEC turns that into a mechanical,
pre-committed test so the mistake cannot recur by optimism.

## The model (deliberately conservative)

Round-trip cost = two sides of fixed costs + size-scaled market impact:

| Component | Per side | Note |
|---|---|---|
| Slippage | 5.0 bps | intraday is worse than delivery's 0.5 bps |
| Brokerage | 3.0 bps | intraday turnover (₹20/lot proxy) |
| STT (sell) | 2.5 bps | charged once |
| Exchange + GST | 1.0 bps | bundle |
| Market impact | 2.0 bps × (size / 1%-ADV) | linear in participation |

The model is intentionally punitive — a gate should kill marginal ideas, not
flatter them (same philosophy as the options-spread haircut).

## The gate (ALL must hold, judged on the strategy's own GROSS per-trade edge)

1. **Net expectancy > 0.05R** per trade after the round-trip cost.
2. **Gross edge > 1.5 × round-trip cost** (a real margin over the cost wall, not a
   photo-finish).
3. **Net Sharpe ≥ 0.80** annualized at the intended trade frequency.

- **PASS** → the idea is *eligible to build* (still subject to normal
  pre-registration, OOS, and paper-forward discipline — the gate is necessary, not
  sufficient).
- **FAIL** → the idea **stays frozen**. No live intraday code is written.

## How to run it

Feed the candidate's gross per-trade returns + risk + intended frequency/size:

```python
from cost_gate import GateInputs, evaluate, format_report
res = evaluate(GateInputs(gross_ret=g, risk=r, trades_per_year=750, size_fraction=0.01))
print(format_report(res, name="my_intraday_idea"))
```

Default verdict is FAIL. Clearing this gate is the ONLY path from frame A to any
frame-B live intraday work.
