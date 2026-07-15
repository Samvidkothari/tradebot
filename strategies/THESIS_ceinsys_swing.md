# THESIS — CEINSYS Single-Name Swing (live-data snapshot)

Companion to `SPEC_ceinsys_swing.md`. The SPEC is the fixed rulebook; this THESIS
records the real-world read at a point in time. Snapshot data is external market
data (Trendlyne / Tickertape), captured **13 Jul 2026**, for context only — the
authoritative backtest numbers come from `ceinsys_analysis.py` run on
`data/CEINSYS.csv`.

## Live snapshot — 13 Jul 2026

| Field | Value |
|---|---|
| Last price | ₹940 |
| 50-DMA | ₹950.5 |
| 100-DMA | ₹993.2 |
| **200-DMA** | **₹1,082.8** |
| 52-week range | ₹796.75 – ₹1,952  (−51.8% from high) |
| Return 3M / 6M / 1Y | −14.4% / −5.0% / −35.9% |
| RSI (day) | 51.3 (neutral) |
| MACD (day) | −9.5, signal −12.3 |
| Beta (1Y) | 1.6 (high) |
| Pivot / S1 / R1 | 936 / 918 / 952 |
| Analyst (Joindre, 03-Jun-26) | Buy, target ₹1,142 (≈+21%, ~12-month) |
| Fundamentals | FY26 rev +58%, PAT +111%, debt-free, order book ₹876 cr, promoter ~51% |

## The read (per the SPEC's own rules)

**Trend gate = FAIL. Current stance: WAIT, do not enter.**

- Price ₹940 sits **below the 50-, 100- and 200-DMA**. The 200-DMA (₹1,082.8) is
  ~15% above the current price. Rule 1 of the SPEC ("long only, and only when
  `close > 200-DMA`") is not met, so the disciplined action is to stand aside.
- The stock is down 36% over 1 year and 51.8% off its 52-week high — a clear
  down-trend, i.e. exactly the regime column B of `ceinsys_analysis.py` shows has
  the worst +20% odds and the deepest drawdowns.
- Good business (growing, debt-free, fresh order wins) does **not** override the
  trend gate. A cheap, improving small-cap can keep falling; the rule exists so we
  don't catch that knife.

## What would flip it to a valid setup

1. A daily **close back above the 200-DMA (~₹1,083)**, or a confirmed
   break-of-structure up-trend per `priceaction.py`; **and**
2. A price-action demand-zone entry with initial stop = entry − 2×ATR(14) and
   reward:risk ≥ 2.5 to the +20% objective.

Until (1) holds, the +20%/5-month objective is a **watch-list item, not a trade**.
Fundamentals are the reason it's on the watch-list; the trend gate is the reason
it isn't a position yet.

## Illustrative mechanics (for teaching, at ₹940 — gate still blocks it)

Entry ₹940, ATR≈₹35 → stop ₹870 (risk ₹70/share), +20% target ₹1,128. On
₹10,00,000 at 1% risk: 142 shares (~₹1.33 L, 13% of capital); win ≈ +₹26.7k
(+2.7% of pot), stop-out ≈ −₹9.9k (−1%). R:R ≈ 2.7:1. **But trend gate = FAIL →
the bot does not take this trade today.**

_Not investment advice. Paper/research only; no orders are placed. Targets are
intent, not guarantees — a −20% outcome is fully in scope, more so below trend._
