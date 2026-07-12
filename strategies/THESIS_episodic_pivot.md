# THESIS — Episodic Pivot (Bonde) governed by the Varma risk-state layer

*Source: distilled from the Pradeep Bonde interview (Episodic Pivots playbook,
$1M → $100M+), combined with the Varma risk doctrine already in this repo. This
is the "why"; locked rules live in `SPEC_episodic_pivot.md`. Paper/research only.*

## The claim

Stocks that make a sharp, volume-backed thrust to a fresh high are "in play" —
money is flowing into them for a reason — and they continue often enough, far
enough, to pay after costs *if* you (a) only trade them when the broad tape
supports it, (b) size for fat tails, and (c) sell into strength instead of
holding for a perfect top. Bonde supplies the offense; Varma supplies the brake.

## Why it should help (the two playbooks, one pipeline)

1. **Trade where the money is (Bonde).** An episodic pivot — a large thrust on
   abnormal volume to a new high — marks a stock the market is actively
   re-rating. That is a higher-base-rate event than a quiet chart pattern.
2. **"Everything works / nothing works" is a regime call (Bonde) = "classify,
   don't predict" (Varma).** Breakouts print money in trending, risk-on tape and
   bleed in choppy or stressed tape. Rather than predict, we *classify* the tape
   with the existing `regime.py` and simply refuse EP entries in a
   mean-reverting or bear+high-vol state. This is Bonde's own off-switch, made
   mechanical by Varma's method.
3. **Sell into strength (Bonde) = fractional-Kelly de-risking (Varma).** Bonde
   books ~80% into strength and rides the rest; Varma sizes down under fat tails.
   Same instinct. We take half off into the first magnitude spike (which tends to
   mean-revert), move to breakeven, and trail the remainder — capturing the
   duration move while removing tail risk early.
4. **Governed offense compounds; ungoverned offense blows up.** Bonde's own
   warning about "god syndrome" after win streaks is exactly what a risk governor
   exists to contain. Layering Varma's graded exposure factor on top caps the
   damage from a cluster of failed EPs in a hostile regime.

## The honest doubt (what would make this fail)

- **No catalyst data — the biggest gap.** Bonde insists a chart with no
  fundamental reason is *not* a setup. This repo has no earnings/news feed, so we
  trade the technical ignition *blind to the catalyst*. Bonde would expect this to
  be materially worse than a catalyst-gated EP. If the blind proxy has no edge,
  that is evidence about the missing data, not necessarily about the playbook.
- **Large-cap, daily-bar, long-only.** The NIFTY-50 cash universe on daily bars
  is the *opposite* of Bonde's small-cap, fast, sometimes-short hunting ground.
  Ignition events will be rarer and tamer; the sample may be too small to judge
  (→ INCONCLUSIVE, honestly reported).
- **Breakouts are the most crowded, most whipsaw-prone entry.** Without the
  catalyst and intraday timing, mechanical breakout entries can bleed on cost and
  false starts. The gate and the sell-into-strength exit exist precisely to
  contain that; if they can't, the thesis fails.

## How it will be judged

By the pre-registered, after-cost criteria in the SPEC, on the **combined
GATE+SIZE** system (not the raw ignition alone), with an out-of-sample split. The
backtest deliberately reports RAW vs +GATE vs +GATE+SIZE so the contribution of
each playbook is visible: the gate should improve expectancy/drawdown even as it
cuts trade count; sizing should smooth drawdown further. Default verdict is
reject; parameters are fixed before the run.
