# SPEC — CEINSYS Single-Name Swing (+20% / ~5-month objective)

**Pre-registered:** 2026-07-13, BEFORE any CEINSYS backtest was run.
Parameters below are FIXED and must not change after seeing results.
Single-name study — treated as descriptive research, not a promotable sleeve
(one small-cap cannot clear the ≥100-trade bar the equity specs require).

---

## 1. Thesis (why might this earn the target?)

Ceinsys Tech (NSE: CEINSYS) is a small-cap geospatial/ER&D compounder: FY26
revenue +58%, PAT +111%, debt-free, ₹876 cr order book, promoter ~51%. The bet
is that a fundamentally improving small-cap in an up-trend can deliver a ~20%
swing over a quarter-to-two-quarters window. The honest doubt: it is thin,
government-payment-cycle sensitive, and more volatile than 90% of Indian peers —
so the same volatility that makes +20% reachable makes −20% equally reachable.
This spec measures both sides rather than assuming the upside.

## 2. Universe & data

Single symbol: `CEINSYS` (custom universe `ceinsys_swing` in universes.json).
Daily OHLCV via `fetch_ceinsys.py` (yfinance `CEINSYS.NS`, ~5y), cached to
`data/CEINSYS.csv` and read through the standard `data_io` cache. Daily bars only.

## 3. Mechanical rules (objective, no look-ahead)

- **Entry filter:** long only, and only when `close > 200-DMA` (trade with trend).
- **Signal:** the PRE-REGISTERED price-action logic in `priceaction.py`
  (market-structure break + demand-zone retrace + R:R ≥ 2.5). No rule is
  reimplemented here; `ceinsys_analysis.py` calls `priceaction.generate_trades`.
- **Initial stop:** `entry − 2.0 × ATR(14)` (config `ATR_STOP_MULT`).
- **Target:** `+20%` (config `TARGET_RET`); optional trail via `trailing_exit.py`.
- **Time stop:** exit after `HORIZON_DAYS = 105` (~5 months) if neither hit.
- **Risk:** `1%` of capital to the initial stop (`RISK_PER_TRADE`); position size
  = floor(capital × 1% / (entry − stop)).

## 4. What is measured (`ceinsys_analysis.py`)

- **A.** After-cost price-action backtest on CEINSYS (expectancy in R, win rate,
  profit factor, compounded, max DD, OOS split at config.SPLIT_DATE).
- **B.** 5-month **target study** on CEINSYS's own history: over every
  105-trading-day forward window, P(touches +20%), P(ends ≥+20%), P(−20% drawdown),
  median / worst forward return — unconditional and conditioned on `close > 200-DMA`.
- **C.** Live plan: latest price, trend state, ATR entry/stop/target, and sizing.

## 5. Honest pass/interpretation criteria

This is a single name, so the promotion rules do NOT apply. Interpretation only:

- The +20% objective is credible **only if** column B shows a materially
  above-even P(touch +20%) *and* the trend-filtered column improves it *and*
  P(−20% drawdown) is tolerable at the chosen size.
- If price is below the 200-DMA, the pre-registered stance is **wait**, not enter.
- No number in this study is a guarantee; a −20% outcome is explicitly in scope.

## 6. Cost & integrity

Costs = `config.COST_ROUNDTRIP` (Zerodha delivery model, unchanged). No orders are
placed anywhere in this repo. Parameters were fixed in this file before the run.
