# THESIS — Residual (Idiosyncratic) Momentum  *(DRAFT — cross-sectional scout)*

*Loop-2 weekly scout draft, 2026-07-09. This is the "why"; the locked rules live
in `SPEC_residual_momentum_DRAFT.md`. Paper/research only — no orders. Default
verdict is reject.*

## The claim

Raw 12-1 cross-sectional momentum is a live-but-decaying sleeve here (OOS Sharpe
≈0.09, 2/4 walk-forward segments negative). The academic repair for *that
specific* failure mode is **residual momentum** (Blitz–Huij–Martens 2011): rank
names on the momentum of their **beta-neutralized** returns — the part of each
stock's move that is *not* explained by the index. The claim is that stripping
market beta removes the dominant common factor that makes raw momentum lurch with
the index and load up on high-beta names right before reversals, leaving a
cleaner, more stationary cross-sectional signal.

## Why it should help (economic + behavioural rationale)

1. **Raw momentum's tail risk is a beta artefact.** Momentum crashes (2009-style
   snap-backs) happen because the raw winners portfolio quietly becomes a
   high-beta bet. Neutralizing beta *before* ranking is designed to cut exactly
   that left tail — the same asymmetric intent the low-vol sleeve already targets.

2. **Idiosyncratic continuation is a distinct effect.** Under-reaction to
   firm-specific information is a different behavioural driver than index trend.
   Isolating the residual is meant to measure that under-reaction directly instead
   of contaminating it with market direction.

3. **Lower, cleaner turnover.** Residual ranks are historically more stable
   month-to-month than raw ranks, so a monthly-rebalanced, large-cap version has a
   fighting chance against the ₹cost model where a high-churn signal would not.

## The honest doubt (what would make this fail — and did)

- **It is still a momentum sleeve.** If the decay in this universe is momentum-*
  family* decay (crowding, post-2024 regime), beta-neutralizing the inputs will
  not manufacture an edge that the family has lost. The prior here is skeptical.
- **Estimation noise in rolling beta.** A 120-day OLS beta is itself unstable in
  the bear/high-vol tape; a noisy beta re-injects market exposure through the back
  door.
- **48-name universe is thin for a residual sort.** Top-10 of ~48 is a coarse
  decile; idiosyncratic dispersion may be too small on mega-caps to rank cleanly.

## How it will be judged

As a return-generating cross-sectional strategy, judged **after costs** against
NIFTY-50 buy-and-hold on the pre-registered OOS split, with a walk-forward check
and a 1.5× cost stress, and held to the multiple-testing OOS-Sharpe haircut in
`SPEC_residual_momentum_DRAFT.md`. Default verdict is reject.

## Result (2026-07-09 gauntlet — pre-registered, one locked parameter set)

REJECTED. It *did* beat the index on raw return (OOS CAGR 12.8% vs 3.9%), but it
**failed the two criteria that matter for this thesis**: OOS max drawdown −24.9%
was *worse* than the index's −15.8% (the beta-neutralization did **not** buy the
promised tail protection), and OOS Sharpe 0.73 (0.71 at 1.5× cost) fell short of
the 0.95 haircut bar. Walk-forward OOS CAGRs `[0.89, −0.23, 0.32, −0.09]` show the
**identical 2/4-negative instability** as the live raw-momentum sleeve. The
in-sample Sharpe of 2.0 collapsing to 0.7 OOS is the same overfit/decay signature
the research assistant already flags on the momentum family. This is momentum-
family decay, not a new edge. Not tuned to rescue.
