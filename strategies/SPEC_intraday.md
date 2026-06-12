# SPEC — Intraday Opening-Range Breakout (paper simulation)

**Pre-registered:** 2026-06-12, BEFORE the simulator was built.
This is **paper-only**: every fill is a simulated row in `intraday.db`. NO real
orders, ever (standing project rule). The agent may run this unattended, but it
can never place a live order.

This is operational *simulation infrastructure*, not a Phase 2B strategy-class
attempt — it does not compete against the NIFTY buy-and-hold benchmark and does
not consume the (closed) Phase 2B budget. It exists so we can watch a disciplined
intraday rule behave on a separate paper book.

---

## 1. What it is, in one sentence

Each trading day, for a small set of liquid stocks: let the first 15 minutes
define a price range, then take the **first** breakout of that range (long if it
breaks up, short if it breaks down), manage it with a stop and a target, and
**square off everything by 15:15** — nothing is held overnight.

## 2. Data

- Source: **yfinance**, `interval="5m"` (the free Kite plan has no intraday
  data). 75 bars/day, 09:15–15:25 IST. ~1 month of history is available.
- Known limits, acknowledged not corrected: yfinance intraday is **delayed
  (~15 min), not tick data, and occasionally patchy**. So simulated fills use
  **bar closes**, never intra-bar prices — a deliberately conservative choice.
- A day is **tradable** for a symbol only if it has the full opening range
  (first three 5-min bars: 09:15, 09:20, 09:25) and at least one later bar.

## 3. Universe (fixed, pre-committed)

Ten liquid NIFTY large-caps:
`RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, AXISBANK, ITC, LT, BHARTIARTL`.
(Small set: intraday yfinance calls are rate-limited, and liquid names make the
bar-close fill assumption least unrealistic.)

## 4. The rule (one parameter set — not to be tuned to results)

- **Opening range (OR):** from the first three 5-min bars (09:15–09:30),
  `OR_high = max(high)`, `OR_low = min(low)`.
- **Entry (first breakout wins, one trade per symbol per day):** scanning bars
  from 09:30 onward, the first bar whose **close > OR_high** → enter **LONG** at
  that close; the first whose **close < OR_low** → enter **SHORT** at that close.
  Whichever triggers first is taken; the day is then done for that symbol.
- **Stop loss:** opposite edge of the range — `OR_low` for a long, `OR_high` for
  a short. If a later bar's close crosses the stop, exit at that close.
- **Target:** `1 × OR_range` beyond entry, where `OR_range = OR_high − OR_low`
  (long target = entry + OR_range; short target = entry − OR_range). Exit at the
  first bar close that reaches it.
- **Square-off:** any position still open at the **15:15 bar** is closed at that
  bar's close (`exit_reason = "EOD"`). No overnight positions, ever.
- Stop is checked before target on a bar that could satisfy both (conservative).

## 5. Sizing (pre-committed)

- `CAPITAL = ₹1,000,000` paper, separate from the low-vol book.
- `MAX_CONCURRENT = 5` — at most 5 open intraday positions at once (first-come).
- Per-trade notional = `CAPITAL / MAX_CONCURRENT = ₹200,000`; `qty = floor(
  notional / entry_price)`. Shorts allowed (intraday). Never deploy cash twice.

## 6. Costs — INTRADAY model (Zerodha MIS, not the delivery model)

Charged on both legs of every round-trip, in rupees:
- **Brokerage:** `min(₹20, 0.03% × leg_notional)` per leg.
- **STT:** 0.025% on the **sell leg only** (intraday equity).
- **Exchange txn:** 0.00297% per leg (NSE). **SEBI:** 0.0001% per leg.
- **Stamp duty:** 0.003% on the **buy leg only**.
- **GST:** 18% on (brokerage + exchange txn + SEBI), per leg.
- **Slippage:** 0.05% per leg (on top, models the bar-close fill being optimistic).

## 7. Ledger & idempotency

- Separate SQLite DB **`intraday.db`** (its own `account`, `trades`, `days`
  tables). Never writes to `portfolio.db`.
- Re-running the same date is harmless: a date already simulated is skipped
  (tracked in `days`), so an unattended daily run cannot double-count.

## 8. What "PASS/FAIL" means here

There is **no pass/fail gate** — this is a monitoring sandbox, not a strategy
audition. The report states net P&L, win rate, average win/loss, and costs as a
share of gross, so the rule's behaviour is visible and honest. If it loses money
in simulation, that is a useful finding, not a failure to be tuned away.

## 9. Autonomy

- The simulator runs **once per day after the close** (e.g. ~15:45 IST on NSE
  trading days), unattended, appending that day's simulated trades.
- It is pure simulation — scheduling it places no orders and touches no broker
  API. Scheduling mechanism is set up separately, only after the simulator is
  tested.
