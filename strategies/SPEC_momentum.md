# SPEC — Cross-Sectional Momentum (NIFTY 50)

**Pre-registered:** 2026-06-12, BEFORE any momentum backtest was run.
No parameter in this file may change after seeing results (Phase 2B Rule 3).
One parameter set per thesis. If it fails the pass criteria, the thesis failed.

---

## 1. Thesis (in plain language — why should this earn excess return?)

**Who is on the other side of the trade, and why do they lose to us?**

Cross-sectional momentum buys the stocks that have gone up the most over the
past year and holds them, rebalancing monthly. The documented premium
(Jegadeesh & Titman 1993, replicated across 40+ countries and in Indian
equities) comes from two persistent sources:

1. **Behavioral underreaction.** When good news arrives (earnings beats,
   upgrades, sector tailwinds), investors update too slowly — anchoring to old
   valuations and the disposition effect (selling winners too early to "lock in
   gains"). Prices drift toward fair value over *months*, not instantly. A
   momentum buyer is paid for stepping in while the crowd underreacts.
2. **Institutional flow / index & mandate effects.** Funds chase relative
   strength and rebalance into recent outperformers on a lag; this flow extends
   trends. The counterparty is the early-seller and the slow-updater.

The premium is **compensation for crash risk**: momentum occasionally suffers
violent reversals (e.g. sharp rebounds off market bottoms, when beaten-down
losers rocket and held winners stall). We accept that tail risk in exchange for
the average premium. This is a *risk-based + behavioral* explanation, not a
data-mined pattern.

**Why would this work on NIFTY large-caps when the SMA crossover didn't?**

The SMA 20/50 crossover is a **time-series (absolute) timing** signal applied to
each stock independently. It repeatedly moves to cash, so it (a) misses the
market's structural upward drift during flat patches and (b) pays transaction
costs on every whipsaw. On efficient, heavily-traded large-caps, short-term
*timing* has no durable edge and costs dominate — exactly what our backtest
showed (+2.3% vs +8.0%, win rate 30%).

Cross-sectional momentum is **relative**, not timing. It is **always fully
invested** in *something* — the strongest names — so it captures the +8% market
drift baseline AND adds the winners-minus-losers spread on top. Its turnover is
**monthly and partial**, not daily flips, so cost drag is far lower per unit of
signal. The edge being harvested (relative strength persistence) is a different
economic phenomenon from the one the crossover failed to find (absolute timing).

This is the honest reason the two can differ. It does not *guarantee* a pass —
that is what the backtest decides.

---

## 2. Universe

- The **current NIFTY 50 constituents** available in `data/` (49 stocks;
  LTIM and TATAMOTORS failed to download and are excluded).
- **Known limitation — survivorship bias:** this is today's membership applied
  to the past. Stocks dropped from the index over the window are absent and
  current members were partly selected for past success. Real-world point-in-time
  results would be somewhat worse. This bias is *acknowledged, not corrected*
  (free-data constraint) and will be restated in the report.

## 3. Signal — momentum score

For each stock at each rebalance date `t`:

    score(t) = close[t - 21] / close[t - 273] - 1

i.e. the **trailing 12-month total return, skipping the most recent 1 month**
(the standard "12-1" momentum; the skip avoids short-term reversal contamination).
Trading-day conventions: 1 month = 21 trading days, 12 months = 252, lookback
window length = 273 trading days.

A stock is **rankable** at `t` only if it has a valid close at both `t-21` and
`t-273` (enough history). Non-rankable stocks are excluded that month.

## 4. Portfolio construction

- **Rebalance frequency:** monthly — on the **first trading day of each calendar
  month**.
- **Holdings:** the **top 15** stocks by score (≈30% of a 50-name universe),
  **equal-weight** (each target weight = 1/15 ≈ 6.67%).
- **Sizing rules / constraints (named constants):**
  - `TOP_N = 15`
  - `MAX_WEIGHT_PER_STOCK = 1/15` (equal-weight; no single name exceeds this)
  - Fully invested when ≥ TOP_N names are rankable; if fewer are rankable, hold
    that many equal-weight and leave the remainder in cash.
  - **Never trade cash that isn't there** — costs are deducted from portfolio
    value before computing new share counts.
- Between rebalances, positions are held and drift with price (no intramonth
  trading).

## 5. Costs (UNCHANGED from the original backtest)

- Same constants as `backtest.py`: brokerage ₹0 (delivery), STT 0.10%/side
  (buy+sell), exchange 0.00345%/side, SEBI 0.0001%/side, stamp 0.015% on buy,
  GST 18% on (brokerage+exchange), slippage 0.05%/side. Round-trip ≈ 0.323%.
- **Turnover modeled explicitly at each rebalance** (this is where momentum
  backtests cheat): compute the rupee value bought and sold to move from current
  holdings to target holdings; charge `bought_value × COST_ENTRY +
  sold_value × COST_EXIT`. Drift-rebalancing of names already held is included
  in turnover.

## 6. Data period & out-of-sample split (UNCHANGED)

- Data: yfinance NSE daily OHLCV (auto-adjusted), 2021-06-08 → 2026-06-11.
- Effective backtest begins after the 273-day warmup (~Aug 2022).
- **Out-of-sample split date: 2024-01-01** (same as original).
  - Period A: warmup-end → 2024-01-01
  - Period B (out-of-sample): 2024-01-01 → present
- Benchmark: NIFTY 50 (`^NSEI`) buy-and-hold over the identical window.

## 7. Pre-committed PASS criteria (all must hold, else FAIL)

1. **Beats buy-and-hold NIFTY 50 after all costs in the FULL period AND
   separately in the out-of-sample period (Period B)** — on CAGR.
2. **Max drawdown no worse than buy-and-hold's** over the same window
   (momentum's drawdown ≥ NIFTY's drawdown, i.e. not more negative).
3. **Statistically meaningful trade count** — at least 30 position-changes
   (entries/exits) across the backtest. A handful of trades that "won" proves
   nothing and is itself a FAIL-flag.

If **any** criterion fails, the verdict is **FAIL**, regardless of how close it
came. The result is committed either way. This is one of the two remaining
strategy-class attempts (Phase 2B Rule 4).

## 8. Live-interface compatibility (Phase 3)

The engine exposes `target_portfolio(panel, date) -> set[symbol]`. The Phase 3
paper adapter computes this target, diffs it against current positions, and
emits BUY/SELL actions — preserving the existing "compute target → diff against
holdings → act" pattern. The per-stock `generate_signal()` for the retired SMA
strategy stays untouched.
