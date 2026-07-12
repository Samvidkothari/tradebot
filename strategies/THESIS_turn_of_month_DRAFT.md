# THESIS — Turn-of-Month Index Seasonality  *(DRAFT — vol / expiry-calendar scout)*

*Loop-2 weekly scout draft, 2026-07-09. This is the "why"; the locked rules live
in `SPEC_turn_of_month_DRAFT.md`. Paper/research only — no orders. Defined-risk
(long index or cash — never short, never levered). Default verdict is reject.*

## The claim

Equity index returns are not uniform across the month. A well-documented
**turn-of-month (ToM)** effect concentrates positive drift in a narrow window
around the month boundary — roughly the last trading day plus the first few of the
next month — driven by calendar-clustered flows (salary/SIP inflows, fund NAV and
mandate rebalancing, month-end marking). The claim: holding the NIFTY-50 basket
**only** inside that window and sitting in cash the rest of the month captures most
of the index's drift with a fraction of the time-in-market, and therefore a much
smaller drawdown.

## Why it should help (economic + behavioural rationale)

1. **Flows are calendar-anchored, not price-anchored.** SIP debits and payroll
   settle on fixed dates; passive funds rebalance to month-end weights. That
   demand is mechanical and recurring — a structural, non-predictive reason for
   clustered drift, in the same "get paid to hold risk at the right time" spirit as
   the Varma overlay.
2. **Defined risk by construction.** The book is only ever long the cash basket or
   flat. No tail beyond the index's own — it passes the Risk Manager's unbounded-
   tail veto trivially, unlike any short-vol structure.
3. **Time out of market caps drawdown.** ~80% of days in cash means the sleeve
   cannot participate in most of a bear leg — the drawdown criterion is the one it
   should pass comfortably.

## The honest doubt (what would make this fail — and did)

- **Cost drag is monthly and brutal.** Full entry + full exit every month is
  ~0.32% round trip × 12 ≈ **3.8%/yr** of headwind on notional. The ToM premium
  has to clear that *before* it beats simply holding the index for free.
- **The effect has weakened.** As ToM became well known, the window premium
  compressed; a 48-name mega-cap basket is exactly where it should be most arbed.
- **Seasonality is a small-sample story.** ~60 month-boundaries in the sample is
  thin; a couple of good Decembers can masquerade as an edge.

## How it will be judged

After costs, against NIFTY-50 buy-and-hold, on the pre-registered OOS split, with
a walk-forward check and a 1.5× cost stress, held to the multiple-testing OOS-
Sharpe haircut in the spec. Default verdict is reject.

## Result (2026-07-09 gauntlet — pre-registered, one locked parameter set)

REJECTED. The drawdown thesis held (OOS max DD −10.6% vs index −15.8%) — being in
cash most of the month *does* cut the tail. But the **return was eaten alive by
cost drag exactly as feared**: OOS CAGR −0.05%/yr at 1× cost and −2.1%/yr at 1.5×,
OOS Sharpe ≈0.03 (−0.23 at 1.5×) against a 1.00 bar. Sitting out most of the month
to save drawdown you were never going to take, while paying 3.8%/yr for the
privilege, is a losing trade after costs. This is the intraday cost lesson in a new
suit — settled evidence that monthly full-basket churn does not survive this cost
model. Not tuned to rescue.
