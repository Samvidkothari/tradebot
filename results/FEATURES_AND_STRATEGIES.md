# Features & Strategies — Reference Catalog

Generated 2026-06-27. **All paper / simulated — no order-placement code exists.**

**Your data universe:** 48 NIFTY 50 stocks + the NIFTY 50 index benchmark
(`data/*.csv`, daily OHLCV via yfinance), **1,240 trading days, 2021-06-23 →
2026-06-25**. (`LTIM`, `TATAMOTORS` excluded — delisted tickers on yfinance.)
Data is **price/volume only** — no fundamentals on the free plan.

---

## Features (factors)

Each factor scores every stock cross-sectionally and is normalised to **[0, 1]
where 1 = most attractive** (direction-aware), so they combine via weighted
composite. Implemented in `factors.py` (`BaseFeature` plug-ins).

| Factor | Direction | What it measures | Economic idea |
|---|---|---|---|
| **momentum** | higher better | 12-1 cross-sectional momentum | Recent relative winners keep winning (1–12 mo) |
| **low_volatility** | lower better | 60-day realized volatility | Calmer stocks earn better risk-adjusted returns |
| **trend** | higher better | price distance above its 200-day MA | Sustained uptrends persist |
| **reversal** | lower better | 5-day return (buy the losers) | Short-term overreaction mean-reverts |
| **vol_compression** | lower better | 10d vol / 60d vol | Compressed volatility precedes expansion |
| **liquidity** | higher better | 20-day avg traded value (close × volume) | Tradability / capacity |

**16 factors total** (expanded from 6), grouped:
- **Technical:** momentum, trend, **atr** (range vol), **adx** (trend strength),
  **relative_strength** (vs NIFTY), **relative_volume** (attention).
- **Statistical:** low_volatility, **volatility** (20d), **zscore** (mean-reversion),
  **beta** (vs NIFTY), **correlation** (vs NIFTY), **trend_persistence** (efficiency ratio),
  reversal, vol_compression.
- **Market:** **sector_strength** (the stock's sector's 63d strength), liquidity.

All are cross-sectional `BaseFeature` plug-ins, normalised to [0,1]. ATR/ADX read
high/low; relative-strength/beta/correlation read the NIFTY benchmark;
sector_strength reads the sector map — all wired through the data layer.

**Illustrative multi-factor composite:** equal weight on `momentum +
low_volatility + trend` (see the dashboard **Factors** / **Feature Store** tabs).
This is research analysis, **not** a tradeable strategy.

**Market breadth** (% of names above their 200-day MA) is a single market-level
number, not a per-stock score — it belongs to the regime engine, not this
cross-sectional library.

**Not built (no data — "future" factors, refused to fabricate):** ROE, ROCE,
EPS Growth, Sales Growth, Quality, Value (P/E, P/B), Operating Margin, Delivery %,
Institutional Buying. These light up only if a fundamentals feed is added.

---

## Strategies

Status legend: ✅ passed · ❌ failed · ⚰️ retired · 🟡 inconclusive (awaiting evidence)

### Equity (monthly rebalance, equal-weight) — `strategy_base.py` plug-ins

| Strategy | Params | Supported regimes | Status | Verdict |
|---|---|---|---|---|
| **Low-Volatility** | hold 15 lowest 60-day-vol names; 60-day warmup | low_volatility, sideways, bear, bull | ✅ **PASSED** | Full +11.1% / OOS +7.6% CAGR vs NIFTY, **lower** drawdown (−15.9%). In Phase 3 paper. |
| **Momentum (12-1)** | top 15 by 12-1 momentum; 273-day warmup | bull, trending | ❌ **FAILED** | Higher CAGR (+14.7%) but −27.2% drawdown (~1.7× NIFTY) — fails the drawdown criterion |
| **SMA 20/50 crossover** | per-symbol trend-following (Phase 2) | — | ⚰️ **RETIRED** | Underperformed NIFTY (+3.3% vs +8.0% CAGR) |

- **Economic rationale — Low-Vol:** the low-volatility anomaly — calmer stocks
  earn higher risk-adjusted returns than CAPM predicts; defensive in drawdowns.
- **Economic rationale — Momentum:** cross-sectional 12-1 momentum — recent
  relative winners persist over 1–12 months; works in trends, vulnerable to sharp
  reversals.

### Intraday (5-min bars, square-off by day end) — monitoring sandbox

| Strategy | Idea | Status | Finding |
|---|---|---|---|
| **ORB** | opening-range breakout | ⚰️ **RETIRED** | Net −₹15,893 — whipsawed by stops; edge < costs |
| **VWAP** | mean-reversion to VWAP | ⚰️ **RETIRED** | Net −₹5,616 — run over on trend days; edge < costs |

Combined gross edge +₹11,405 vs costs −₹32,915 over ~10 days → a thin intraday
edge does not survive realistic transaction costs.

### Options (forward paper, model-priced) — VRP harvest, head-to-head

| Strategy | Structure | Worst case | Status |
|---|---|---|---|
| **Short Strangle** | sell 4%-OTM call + put, monthly | **unlimited** | 🟡 **INCONCLUSIVE** |
| **Iron Condor** | strangle + 6%-OTM protective wings | **capped (~₹30k)** | 🟡 **INCONCLUSIVE** |

Both open only on a monthly expiry ≥21 days out (v2 fixed-duration), priced with
Black–Scholes on 20-day realized vol, harsh 10%/leg spread. Verdict is withheld
until a real **≥4% NIFTY day** while short — none has occurred yet.

---

## Where to see each live
- **Backtests / Tear Sheets** tabs — equity strategy metrics, walk-forward, Monte Carlo
- **Factors** tab — current factor leaderboards + composite
- **Regime** panel (Tear Sheets) — which strategies are in/out of the current regime
- **Options** tab — strangle & condor paper books
- **Risk Analytics / Attribution / Portfolio Analysis** tabs — risk + return decomposition

## Standing rules
Every strategy is **pre-registered** (thesis + spec committed before any test),
gets **one parameter set** (no result-driven re-tuning), and is judged **after
costs** against NIFTY 50 buy-and-hold with a 2024-01-01 out-of-sample split.
**Nothing places a live order.** See `STRATEGY_REVIEW.md` for full verdicts and
`TECH_DEBT.md` for engineering decisions.
