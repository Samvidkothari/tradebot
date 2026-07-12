# THESIS — Breadth-Gated Low-Vol Exposure  *(DRAFT — regime/overlay scout)*

*Loop-2 weekly scout draft, 2026-07-12. This is the "why"; the locked rules live
in `SPEC_breadth_gated_lowvol_DRAFT.md`. Paper/research only — no orders.
Default verdict is reject.*

## The claim

The 7/09 scout memo identified the most promising untested lane on this data:
**low-turnover, regime-conditional sizing of the existing low-vol sleeve**,
rather than another standalone price signal. This is that idea. The claim:
**cross-sectional breadth** — the fraction of the 48-name universe trading above
its own 200-day average — is a slow, robust regime tag that leads the drawdowns
low-vol still eats (−15.9% max DD, and a decaying OOS edge). Scaling the sleeve's
gross exposure down in narrow tapes (few names participating) should cut left
tail materially while giving up little compounding, because breadth regimes
persist for months and the gate only acts twelve times a year.

## Why it should help (economic + behavioural rationale)

1. **Breadth is information the sleeve's own signal cannot see.** Realized vol is
   per-name and backward-looking ~3 months; breadth is a market-internals
   measure. Bear markets begin with narrowing participation before index-level
   vol spikes — the classic internals lead. The live `regime_overlay` keys off
   index trend + vol; breadth is a *different* observable, so this is not a
   re-test of the same overlay.
2. **It attacks the only budgeted weakness.** Low-vol passed on return and DD vs
   index, but −15.9% is most of the blueprint's −12% portfolio DD budget. An
   overlay that converts tail into cash is worth more to the master book than a
   new correlated return stream.
3. **Cost-safe by construction.** The gate is evaluated only at the monthly
   rebalance already being traded; a factor change trades the *delta* of
   exposure. No new trading days are introduced — precisely the structure the
   turn-of-month rejection says survives the ₹cost model.

## The honest doubt (what would make this fail)

- **Every de-risking gate pays an insurance premium.** In a grinding-up tape the
  gate will be wrongly half-invested sometimes; if NIFTY's post-2024 chop keeps
  breadth mid-range, the gate whipsaws at the monthly frequency and drags CAGR
  below the index. That is the likeliest failure.
- **Breadth on 48 mega-caps is coarse.** With ~48 names, breadth moves in ~2%
  steps and is dominated by sector blocks (banks). Thresholds may effectively be
  a noisy index-trend filter — duplicating `regime_overlay` with extra steps.
- **The Sharpe bar is probably unreachable for a defensive overlay.** The
  multiple-testing haircut stands at **1.10** OOS Sharpe; the un-gated sleeve is
  nowhere near that OOS. Per the charter the bar binds anyway. The varma_riskstate
  precedent (overlays judged on drawdown/return-to-worst, not standalone Sharpe)
  is recorded in the SPEC as secondary evidence for the human reviewer — but the
  formal verdict here follows the charter: clear the bar or be rejected.

## How it will be judged

After costs, on the pre-registered OOS split, walk-forward, and 1.5× cost
stress, vs BOTH baselines: NIFTY-50 buy-and-hold and the un-gated low-vol
replica (same engine, factor ≡ 1.0). Formal verdict per the charter haircut
(OOS Sharpe ≥ 1.10 + DD no worse than index). Overlay-merit metrics (ΔmaxDD,
return/|maxDD| vs un-gated) are recorded for the human but cannot flip a REJECT.
Default verdict is reject; no re-tuning under any result.
