# THESIS — Does a DEFINED-RISK short (Iron Condor) beat the naked strangle for us?

**Written:** 2026-06-16, before any iron-condor code. Builds on
`THESIS_options.md` (the VRP thesis) and `[[options-discipline]]`. Same test of a
thesis: *why would this structure earn excess return after costs, and who is on
the other side?* "It's safer" is not, by itself, a reason it makes money.

## Why this structure at all (the honest motivation)

The naked short strangle's real defect is **not** the recent paper mark — it is
**unlimited tail risk**. `THESIS_options.md` §why-it-dies-2 already states it: you
collect small premiums for months, then one budget-day gap or global shock returns
multiples in a single session. That same tail is exactly why **fully-autonomous
short options is a firm NO** — the loss arrives faster than any kill switch.

An **Iron Condor** keeps the two short legs (sell OTM put + sell OTM call) but
**buys a further-OTM wing on each side** (long deeper put + long deeper call). The
bought wings cap the maximum loss to a known, finite number. So the condor is the
*only* short-premium structure that is even theoretically compatible with the
no-ruin line.

## The edge is the SAME edge — harvesting the Volatility Risk Premium (VRP)

Nothing new is claimed about *where* money comes from. It is still: implied vol
prices above realized vol; option buyers overpay on average; we are the net
seller collecting that premium. Who's on the other side of the SHORT legs is
unchanged from `THESIS_options.md` — hedgers (price-insensitive insurance buyers)
and lottery-preference retail. That part passes.

## Who is on the other side of the WINGS we BUY — and why it hurts us

This is the new, uncomfortable part and it must be faced head-on.

To buy the protective wings, **we become the option BUYER** on the far-OTM
strikes — i.e. we step onto the *losing* side of the very premium we just argued
people overpay for. The deep-OTM tails are where lottery-buyer overpricing is
**worst**, so the insurance we buy to cap our risk is itself the **most
overpriced** thing on the board. We pay away VRP on the wings to harvest VRP on
the bodies. The net credit is therefore structurally **smaller** than the naked
strangle's, and shrinks fastest exactly where we most want protection.

## Why it probably dies for US (burden of proof stays on the thesis)

1. **Double the spread — the edge's known cause of death, doubled.**
   `THESIS_options.md` §why-it-dies-1: the bid-ask spread is *where the harvestable
   ~1–2 vol points of edge already mostly dies* with **two** legs. An iron condor
   has **four** legs, each crossed on entry and exit → roughly **2× the spread
   bleed**. The single biggest risk to this thesis is that doubling the spread
   turns a thin edge negative outright. The deep wings are also *less liquid* than
   the bodies, so their spread is proportionally **worse**, not equal.

2. **Safety is bought, not free — and may cost more than the tail it removes.**
   Capping the loss means collecting less every single month. Over many quiet
   cycles you give up real premium continuously to insure against a rare event.
   It is entirely possible the cumulative premium foregone exceeds the one
   catastrophe avoided — i.e. the condor is *safer but lower-expectancy* than the
   strangle. **Safer ≠ better.** That trade-off is the empirical question, not an
   assumption to wave through.

3. **Pin/whipsaw risk on four strikes.** More strikes = more ways for a choppy
   expiry to land badly between legs. Secondary, but real.

## What the condor genuinely buys (the one thing in its favor)

A **known, finite worst case.** No single gap can make the book go negative.
This is not a profit argument — it is a *survivability* argument, and it is the
only basis on which any short-premium structure could ever earn the right to be
proposed for semi-automatic (propose/approve) live trading. The naked strangle
can never earn that right because its worst case is unbounded. So even if the
condor's expectancy is *slightly* lower, it may still be the only *deployable*
member of this family.

## Provisional verdict (default = skepticism)

**The condor is the only options structure consistent with our firm no-ruin
line, but its profit edge is almost certainly THINNER than the naked strangle's**
— it pays double spread and buys back overpriced tails. The honest expectation:
after a harsh, spread-inclusive cost model, the net credit may not survive at all.
That is a finding worth proving, not assuming.

The cleanest honest test on free data is the same as before: a **FORWARD paper
book**, model-priced (BS, 20d realized vol as IV proxy), with a **deliberately
harsh modeled spread charged on ALL FOUR legs**, run on NIFTY (most liquid →
best-case for the structure), held **through a real volatility event** before any
verdict. Crucially this runs **side-by-side with the existing naked strangle on
the same days**, so the question is answered head-to-head: *does giving up the
tail risk cost us the edge, or not?* See `SPEC_condor.md` (to be written next).

## Hard lines (unchanged, restated)

- Paper-only, model-priced, FORWARD. **No real orders, ever.**
- **No fully-autonomous live options trading, ever.** Even if it one day earned
  the right to go live, it would be **semi-automatic: bot proposes, human
  approves** — never autonomous. See `[[options-discipline]]`.
- One pre-committed parameter set. If it fails, the thesis failed — **no
  result-driven re-tuning** (`[[tradebot-constraints]]` Phase 2B).
