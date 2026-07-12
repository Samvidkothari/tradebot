> **⛔ Naked short strangle RETIRED 2026-07-08** (unbounded tail; see
> `SPEC_options.md` banner). The options-edge question this thesis opened is now
> pursued only through the **defined-risk iron condor** (`THESIS_condor.md` /
> `SPEC_condor.md`). This thesis is kept for the audit trail.

# THESIS — Is there an options edge for us? (write this BEFORE any structure)

**Written:** 2026-06-13, before any options code. The test of a thesis: *why
would this structure earn excess return after costs, and who is on the other
side?* "I collect premium and hope" is not a thesis.

## The only candidate edge: the Volatility Risk Premium (VRP)

Across most markets and most of history, **implied volatility prices higher
than realized volatility** — option buyers, on average, overpay. If there is an
options edge available to us, it is harvesting that premium by being a net
**seller** of options.

Corollary: **long volatility is rejected outright.** Buying straddles/strangles
("buy convexity and hope for a move") has *negative* expected carry — you bleed
theta waiting for a move you cannot reliably time. That fails the thesis test by
definition. So any edge must come from the SHORT side.

## Who is on the other side, and why do they keep paying?

- **Hedgers** — funds/corporates buying downside protection. Price-insensitive
  by design: they buy insurance to meet mandates and to sleep at night, knowingly
  overpaying, the way one overpays for home insurance.
- **Lottery-preference buyers** — retail buying cheap OTM weeklys for a 10-bagger.
  Behavioural finance is unambiguous that people overpay for low-probability,
  high-payout bets. NSE retail option volume is among the largest on earth.

This is a genuine other side: we would be the **insurer** collecting premium from
people who rationally (hedgers) or behaviourally (lottery buyers) prefer to pay
it. It persists because the *demand* is structural, not a fad. → Passes the
"who's on the other side" bar.

## Why it probably dies for US (the burden of proof is on the thesis)

1. **The bid-ask spread eats the edge.** Harvestable index VRP is ~1–2 vol
   points; NSE round-trip spreads on anything but the most liquid ATM NIFTY
   strikes are often wider than that, and stock options are far worse. Filled at
   mid, a backtest looks great; filled at the real spread, the edge likely
   vanishes. **The honest question is not "is there VRP" (yes) but "does any
   survive the spread" — prior: mostly no.**
2. **The premium is the fair price of disaster insurance, not free money.** Short
   premium is structurally short a crash. You collect small premiums for months,
   then a budget-day gap or global shock returns multiples in one session. A
   strategy that "works" for 11 months is accumulating an unpaid liability. This
   is exactly why **fully-autonomous short options is unacceptable** — the loss
   arrives faster than any kill switch.
3. **Covered calls tax the wrong book.** Selling calls on the low-vol names that
   just passed caps the quiet upside drift that made them work, for thin (low-IV)
   premium. Unattractive before costs.

## Provisional verdict

**The thesis is real but thin; the default expectation is that after honest,
spread-inclusive costs the edge is gone.** That is a finding worth proving, not
assuming. The cleanest honest test we can run on free data is a FORWARD paper
book with a deliberately harsh modeled spread, on the most liquid instrument
(NIFTY index options), held through a real volatility event before any verdict.
See `SPEC_options.md`.
