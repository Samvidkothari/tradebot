# SPEC — Low-Volatility Anomaly (NIFTY 50)

**Pre-registered:** 2026-06-12, BEFORE any low-volatility backtest was run.
No parameter in this file may change after seeing results (Phase 2B Rule 3).
One parameter set per thesis. If it fails the pass criteria, the thesis failed.

This is the **second and final** permitted strategy-class attempt (Phase 2B Rule 4).
If it FAILS, the conclusion is settled: **buy-and-hold wins.**

---

## 1. Thesis (in plain language — why should this earn excess return?)

**Who is on the other side of the trade, and why do they lose to us?**

The low-volatility anomaly is one of the most robust findings in finance: over
long horizons, the **least volatile** stocks have delivered returns comparable
to — and on a *risk-adjusted* basis clearly better than — the most volatile
stocks. This flatly contradicts the textbook "more risk = more return" (CAPM).
It has been documented across decades, dozens of countries, and Indian equities.
The persistent drivers:

1. **Leverage constraints + lottery preference.** Many investors (mutual funds,
   retail) cannot or will not use leverage to amplify safe, boring stocks. To
   chase high returns they instead crowd into high-volatility, "lottery-like"
   names hoping for a big payoff. That demand **overprices** volatile stocks and
   leaves the calm, dull ones relatively cheap. We are paid to hold the dull ones
   the crowd ignores.
2. **Benchmarking / agency.** Fund managers are judged against an index and are
   rewarded for beating it in up markets, so they tilt toward high-beta names to
   amplify the benchmark. This systematically bids up high-vol stocks. The
   counterparty is the manager reaching for beta and the retail buyer reaching
   for the lottery ticket.

This is a **risk-based + behavioral mispricing** explanation, not a data-mined
pattern. The premium is structural: it comes from a distortion in *who is allowed
to bear risk how*, which persists because the constraints (no leverage, benchmark
pressure) persist.

**Why is this a genuinely DIFFERENT class from the two that already failed?**

- The **SMA crossover** (RETIRED) was *absolute timing* — when to be in vs out of
  a single stock. It missed market drift and paid costs on whipsaws.
- **Cross-sectional momentum** (FAILED, attempt 1) was *relative strength
  persistence* — buy what went up most. It beat the index on return but took
  ~1.7× the drawdown, because winners-chasing is itself a high-risk posture.
- **Low-volatility** ranks on **risk itself**, not on past return at all. Its
  edge comes from a different economic source (leverage/lottery distortion), and
  by construction it holds the *calmest* names — so it has a real, principled
  shot at the **drawdown criterion momentum failed**. Crucially, this is NOT
  momentum-with-a-risk-overlay (which would be banned tuning of a dead thesis);
  the ranking variable, the rationale, and the counterparty are all different.

**Honest caveat, stated up front.** The low-vol anomaly's strongest claim is
about *risk-adjusted* return (Sharpe), and about *not sacrificing* raw return.
Our pass criteria (below, unchanged from prior specs) demand it beat the index on
**raw CAGR** in both periods AND have a smaller drawdown. That is a high bar that
the thesis does not *guarantee* — low-vol can match the index on return while
winning on risk, which under these criteria would still be a FAIL. The criteria
are not being softened to fit the thesis; pre-registration means the bar is the
bar. The backtest decides.

---

## 2. Universe

- The **current NIFTY 50 constituents** available in `data/` (48 stocks).
- **Known limitation — survivorship bias:** today's membership applied to the
  past; dropped names are absent and current members were partly selected for
  past success. Real-world point-in-time results would be somewhat worse. This
  bias is *acknowledged, not corrected* (free-data constraint) and restated in
  the report.

## 3. Signal — realized volatility

For each stock at each rebalance date `t`:

    vol(t) = standard deviation of the last VOL_LOOKBACK daily simple returns

where a daily simple return is `close[i] / close[i-1] - 1`. Lower `vol(t)` =
calmer stock = more desirable.

- `VOL_LOOKBACK = 60` trading days (≈ 3 months). Long enough for a stable
  estimate, short enough to reflect the stock's *current* risk regime. This is
  the single pre-committed window; it will not be tuned to results.
- A stock is **rankable** at `t` only if it has `VOL_LOOKBACK + 1 = 61` valid
  consecutive closes ending at `t` (enough to form 60 returns). Non-rankable
  stocks are excluded that month.

## 4. Portfolio construction

- **Rebalance frequency:** monthly — on the **first trading day of each calendar
  month**.
- **Holdings:** the **15 lowest-volatility** stocks (`vol` ascending),
  **equal-weight** (each target weight = 1/15 ≈ 6.67%).
- **Sizing rules / constraints (named constants):**
  - `TOP_N = 15` (same N as the momentum spec, for a like-for-like comparison)
  - `MAX_WEIGHT_PER_STOCK = 1/15` (equal-weight; no single name exceeds this)
  - Fully invested when ≥ TOP_N names are rankable; if fewer are rankable, hold
    that many equal-weight and leave the remainder in cash.
  - **Never trade cash that isn't there** — costs are deducted from portfolio
    value before computing new share counts.
- Between rebalances, positions are held and drift with price (no intramonth
  trading).

## 5. Costs (UNCHANGED from the original backtest)

- Same constants as `backtest.py`: brokerage ₹0 (delivery), STT 0.10%/side,
  exchange 0.00345%/side, SEBI 0.0001%/side, stamp 0.015% on buy, GST 18% on
  (brokerage+exchange), slippage 0.05%/side. Round-trip ≈ 0.323%.
- **Turnover modeled explicitly at each rebalance:** compute the rupee value
  bought and sold to move from current holdings to target holdings; charge
  `bought_value × COST_ENTRY + sold_value × COST_EXIT`. Drift-rebalancing of
  names already held is included in turnover. (Low-vol turnover is expected to be
  LOW — the calm names are persistent — which is part of the thesis's appeal.)

## 6. Data period & out-of-sample split (UNCHANGED)

- Data: yfinance NSE daily OHLCV (auto-adjusted), 2021-06-08 → 2026-06-11.
- Effective backtest begins after the 61-day warmup (~Sep 2021). Note this gives
  low-vol a longer full window than momentum had (momentum needed 273-day
  warmup); each strategy is judged against NIFTY over **its own live window**, so
  the comparison stays fair. The OOS split date is identical.
- **Out-of-sample split date: 2024-01-01** (same as all prior specs).
  - Period A: warmup-end → 2024-01-01
  - Period B (out-of-sample): 2024-01-01 → present
- Benchmark: NIFTY 50 (`^NSEI`) buy-and-hold over the identical window.

## 7. Pre-committed PASS criteria (all must hold, else FAIL)

1. **Beats buy-and-hold NIFTY 50 after all costs in the FULL period AND
   separately in the out-of-sample period (Period B)** — on CAGR.
2. **Max drawdown no worse than buy-and-hold's** over the same window
   (low-vol's drawdown ≥ NIFTY's drawdown, i.e. not more negative).
3. **Statistically meaningful trade count** — at least 30 position-changes
   (entries/exits) across the backtest.

If **any** criterion fails, the verdict is **FAIL**, regardless of how close it
came. The result is committed either way. This is the **final** strategy-class
attempt (Phase 2B Rule 4); a FAIL here closes the search with the conclusion
"buy-and-hold wins."

## 8. Live-interface compatibility (Phase 3)

The engine will expose `target_portfolio(panel, date) -> list[symbol]` (the 15
lowest-vol names), mirroring the momentum module. The Phase 3 paper adapter
computes this target, diffs it against current positions, and emits BUY/SELL
actions — the same "compute target → diff against holdings → act" pattern.
Phase 3 only begins if a strategy PASSES.
