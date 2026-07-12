# THESIS — Varma Risk-State Exposure Sizer

*Source: distilled from the Dr. Samir Varma interview (physicist / systematic
futures trader, ~30 yrs). This is the "why"; the locked rules live in
`SPEC_varma_riskstate.md`. Paper/research only — no orders.*

## The claim

You cannot reliably **predict** market returns, but you can **classify** the
market's current risk *state* from observable, explainable measures, and size
exposure accordingly. Doing so shrinks the left tail more than it shrinks the
mean, improving risk-adjusted return on any long-equity book it overlays.

## Why it should help (economic + behavioural rationale)

1. **Prediction is arbitraged; risk exposure is not.** Varma's view: alpha decays
   as everyone finds the same signal, but being *paid to hold risk* — and holding
   less of it when risk is high — is durable. This overlay never forecasts
   direction. It reads state and reacts. That is a fundamentally different (and
   more robust) bet than a market-timing model.

2. **Returns are leptokurtic.** Fat tails mean standard deviation understates the
   danger, and the worst days cluster in identifiable regimes (falling trend +
   elevated *relative* volatility). Cutting size in that state trades a little
   upside for a materially smaller left tail — the same asymmetric intent as the
   low-vol strategy this can sit on top of.

3. **Position sizing is the real risk lever.** Varma sizes off *acceptable
   drawdown*, informed by Kelly but at a heavy haircut (a fraction of Kelly),
   because full Kelly is ruinous under fat tails and estimation error. A smooth,
   graded de-risk beats an on/off timer that whipsaws at a single threshold.

4. **Congruence with the existing book.** The live `regime_overlay` already
   encodes the extreme-stress case as a binary 1.0/0.5 switch. This thesis says
   the *same evidence*, read as a continuum, lets you lean out gradually as risk
   builds — before the binary switch would ever fire — while never being *more*
   aggressive than the current overlay in the extreme state.

## The honest doubt (what would make this fail)

- **Regime lag.** Classification is backward-looking; a fast crash can arrive
  before the state flips. This is a brake, not a timer — it will not dodge a
  one-day gap. It must earn its keep over *episodes*, not single days.
- **It can cost money in calm bulls.** Trimming to ~0.90 in benign states gives
  up a little compounding. If drawdown reduction doesn't pay for that forgone
  upside on a risk-adjusted basis, the overlay is not worth running.
- **Over-classification is just prediction in disguise.** Kept deliberately to
  three transparent axes (trend, relative vol, breadth) with locked weights, so
  it cannot be quietly tuned into an overfit forecaster.

## How it will be judged

Not by a standalone Sharpe (it generates no signal of its own). Like the live
overlay: run it forward on a **shadow book** against the un-sized book and the
current binary overlay, and compare **drawdown and return-to-worst-drawdown over
≥1 stress episode**. Passes only if it cuts the left tail without giving back
more than it saves on a risk-adjusted basis. Default verdict is reject.
