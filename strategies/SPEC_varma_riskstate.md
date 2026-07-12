# SPEC — Varma Risk-State Exposure Sizer (graded defensive overlay)

**Pre-registered 2026-07-08, BEFORE any forward evaluation.** Parameters below
are LOCKED. If the overlay disappoints, the overlay fails — no re-tuning to
results. One parameter set per thesis (Phase 2B Rule 3).

Status: **CANDIDATE**. Not wired into `paper_trader.py`. Does not modify or
replace the live `regime_overlay` (SPEC_lowvol_regime_overlay.md). Awaiting a
human decision to run it as a shadow overlay. Paper only; no order code.

## What it is

A **graded sizing overlay**. Given NIFTY closes (and, optionally, the close
panel for a breadth read), it returns a single exposure factor in
**[FLOOR, 1.00]** to multiply every target position by. It classifies the
current risk *state* — it never predicts returns. Implemented in
`varma_riskstate.py` as a pure function; the regime read reuses `regime.classify`
(unchanged).

## What it is NOT

- It does **not** select names, change cadence, or touch any pre-registered
  strategy or `backtest_*.py`. It only scales *size*.
- It **never increases** exposure: the factor is capped at 1.00.
- It does **not** replace the live binary overlay. It is a separate candidate,
  designed to be a **strict generalization**: in the exact bear + ≥85th-pctl-vol
  state, this sizer is capped at 0.50 (`STRESS_CAP`), i.e. always ≤ the live
  overlay there, and additionally trims gently at lower risk levels.

## Locked parameters

| Parameter | Value | Meaning |
|---|---|---|
| `W_TREND` | **0.50** | weight on the trend axis |
| `W_VOL` | **0.40** | weight on relative vol-regime (leptokurtic tail proxy) |
| `W_BREADTH` | **0.10** | weight on participation (dropped + weights renormalized if no panel) |
| `TREND_RISK` | **bull 0.0 / sideways 0.5 / bear 1.0** | trend → risk contribution |
| `BREADTH_RISK` | **broad 0.0 / mixed 0.5 / narrow 1.0** | breadth → risk contribution |
| vol risk | **= `vol_percentile_1y`** | the classify() percentile is the vol contribution directly |
| `FLOOR` | **0.40** | minimum exposure — sizes down, never fully to cash |
| `EXTREME_VOL_PCTL` | **0.85** | tail-brake vol threshold (matches live overlay) |
| `STRESS_CAP` | **0.50** | hard cap in bear + extreme vol (matches live overlay) |
| `GRID` | **0.05** | factor rounded to this grid for operational stability |
| `NEUTRAL_FACTOR` | **0.75** | fail-safe when the risk state can't be read (assume ELEVATED) |

## Rules (all objective)

1. **Risk state** `R ∈ [0,1]` = weighted average of the three axes' risk
   contributions (weights renormalized to trend+vol when no breadth panel).
   `R = 0` is maximally calm, `R = 1` maximally dangerous.
2. **Graded exposure** = `FLOOR + (1 − FLOOR) · (1 − R)`, a linear de-risk.
3. **Leptokurtic tail brake:** if trend = bear AND `vol_percentile_1y ≥ 0.85`,
   cap the factor at `STRESS_CAP = 0.50`.
4. **Snap** to `GRID`, clamp into `[FLOOR, 1.00]`.
5. **Fail-safe:** any missing/short data → `NEUTRAL_FACTOR = 0.75` (never raises,
   never exceeds 1.00).

## Design invariants (pinned by `test_varma_riskstate.py`)

- Factor is always in `[FLOOR, 1.00]`.
- Factor is **monotone non-increasing** in the risk state.
- In the live overlay's stress state, this factor is **≤ 0.50** (strict
  generalization / never more aggressive than the incumbent there).
- Never raises on bad input; degrades to `NEUTRAL_FACTOR`.

## Pass criteria (judged forward, after costs, on a shadow book)

Generates no standalone P&L, so it is judged as a risk overlay, not a strategy:
1. Over ≥1 stress episode, the sized book's **max drawdown** is smaller than the
   un-sized book's.
2. It does not give back more return than it saves — **return / |max drawdown|**
   (or Sharpe) is **≥** the un-sized book's over the full evaluation window.
3. It is **no more aggressive** than the live binary overlay in any state
   (guaranteed by construction; audited on the live NIFTY series).

Anything else = FAIL. Default verdict is reject.

## Audit trail

If promoted to a shadow run, each decision (factor, risk_score, components,
regime tags, reason) is logged to `portfolio.db` meta (`last_varma_riskstate`)
alongside the incumbent `last_regime_overlay`, so the two can be compared
decision-by-decision before any promotion.
