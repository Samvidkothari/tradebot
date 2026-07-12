# THESIS — Low-Vol × Momentum Rank Blend  *(DRAFT — cross-sectional scout)*

*Loop-2 weekly scout draft, 2026-07-12. This is the "why"; the locked rules live
in `SPEC_lowvol_momentum_blend_DRAFT.md`. Paper/research only — no orders.
Default verdict is reject.*

## The claim

The two return-generating edges this desk has ever found on this universe are
low-volatility (live, passed) and 12-1 momentum (real CAGR, failed on drawdown,
now decaying). Both are single-sort portfolios that inherit their factor's whole
failure mode: low-vol gives up upside in strong tapes; momentum quietly becomes a
high-beta crash bet. The claim is that a **50/50 percentile-rank blend** — hold
the 15 names that are jointly stable AND persistent — captures momentum's
continuation premium only in names whose realized vol says the position can be
held through noise, i.e. "defensive momentum". This is a *composite sort*, not a
new factor: the marginal information is in the interaction.

## Why it should help (economic + behavioural rationale)

1. **Momentum's failure here is concentrated in its risk profile, not its mean.**
   The register's own evidence: raw 12-1 earns +13–15% CAGR but with −27% DD and
   an OOS Sharpe collapse. Conditioning the sort on low realized vol excludes
   exactly the high-beta winners that drive momentum crashes — the same intent as
   `momentum_governed`, applied in the cross-section (name selection) instead of
   at the exposure dial.

2. **Low-vol's weakness is the mirror image.** The live sleeve's OOS edge is
   drifting (CAGR ratio 0.67, watch advisory). Its laggards are stale, trendless
   names held only because they are quiet. A momentum tilt inside the quiet half
   replaces dead weight with quiet compounders.

3. **Turnover stays monthly and modest.** Rank blends are more stable than either
   raw rank (a name must move in BOTH sorts to churn), so the ₹cost model — which
   just killed turn-of-month via churn — should bite less here than for pure
   momentum.

## The honest doubt (what would make this fail)

- **Both parents are decaying on this tape.** research_assistant flags material
  IS→OOS decay on low-vol *and* momentum. A blend of two fading signals can
  easily be a fading blend; the multiple-testing bar (1.05 OOS Sharpe) is set
  precisely so that only a genuine interaction effect passes.
- **48 mega-caps is a thin cross-section.** Top-15 of ~48 with two filters may
  converge on nearly the same book as the live low-vol sleeve — in which case
  the Risk Manager's >0.8 correlation veto applies and the idea is redundant
  even if it "passes".
- **Momentum-family decay may dominate.** Residual momentum (tested 7/09) showed
  the family signature: IS Sharpe 2.0 → OOS 0.7. If the interaction term is
  noise, this blend inherits that signature.

## How it will be judged

As a return-generating cross-sectional strategy, after costs, vs NIFTY-50
buy-and-hold, on the pre-registered OOS split (`config.SPLIT_DATE`), with a
walk-forward check and a 1.5× cost stress, held to the multiple-testing haircut
in the SPEC (OOS Sharpe ≥ 1.05, locked before testing). Default verdict is
reject; no re-tuning under any result.
