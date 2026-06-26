# SPEC — Forward VRP Short-Strangle PAPER test (NIFTY)

**Pre-registered:** 2026-06-13, before the options simulator was written.
Implements the test proposed in `THESIS_options.md`. **Paper-only, model-priced,
FORWARD** (no historical option data exists on free plan, so no backtest — and a
model-priced backtest cannot test the spread, which is the whole question).
NO real orders, ever. NO fully-autonomous live trading, ever (firm line: a gap
makes the account negative faster than any kill switch).

**v2 amendment (2026-06-26) — fixed-duration entry (methodology fix, NOT a
result-driven tweak).** v1 opened a cycle on the nearest monthly expiry ≥7 days
out whenever flat, which let the first cycle open only ~9 trading days before
expiry — a near-worthless stub (₹1,211) — while a normal cycle collects ~12×
that. Cycle durations (and therefore the short tail risk) were not comparable.
Fix: open only on a monthly expiry **≥ `OPEN_MIN_DTE = 21` calendar days** out
(roll to the next monthly otherwise), so every cycle opens with ~21–50 days of
life and no stubs. This changes ONLY entry timing — OTM %, spread, stop, sizing,
and the INCONCLUSIVE-until-a-vol-event verdict gate are all unchanged. The
forward book was reseeded fresh on adoption so every cycle is on one rule set.
Residual: monthly expiries are discrete (~30d apart), so some duration variance
remains; perfectly fixed-T would need weekly options. Supersedes the v1 code
constant `MIN_DTE = 7` (the implicit ≥7-day open rule).

## 1. What it is

A monthly **short strangle on NIFTY** — the most liquid Indian option, which
gives the short-VRP thesis its *best honest shot*. If it cannot survive a harsh
modeled spread even here, stock options are hopeless and the search closes.

- Each monthly cycle: **sell 1 OTM call + 1 OTM put** on NIFTY, hold to monthly
  expiry, settle at intrinsic. Mark-to-model daily in between.
- We are the insurer; we keep the premium if NIFTY stays between the strikes.

## 2. Instrument, data, pricing

- **Underlying:** NIFTY 50 (`^NSEI` live / `data/NIFTY50.csv`). Index options only.
- **No real IV available.** Legs are priced by **Black–Scholes using 20-day
  realized vol** as the IV proxy. NOTE this is **conservative for a seller**:
  real IV > RV (that gap *is* the VRP), so RV-priced premium is *less* than a real
  seller collects. The model therefore *understates* the edge — it will not
  flatter the strategy.
- Monthly expiry = **last Thursday** of the month (NSE convention).

## 3. The rule (one pre-committed parameter set — not tuned to results)

- **Entry:** when flat and a new cycle is available, sell the call at the strike
  nearest `spot × (1 + OTM_PCT)` and the put nearest `spot × (1 − OTM_PCT)`, with
  `OTM_PCT = 0.04` (4% OTM) and strikes rounded to the nearest `STRIKE_STEP = 50`.
  Time to expiry `T` = trading days to the last Thursday.
- **Premium received** (per leg) = BS model price − **spread haircut** (§4).
- **Hold to expiry:** settle each leg at intrinsic (`max(0, S−K)` call, `max(0,
  K−S)` put). Cash-settled — no spread on expiry settlement.
- **Stop guardrail:** if open mark-to-model loss reaches `STOP_MULT × premium_
  received` with `STOP_MULT = 2.0`, close both legs early (paying the exit spread
  haircut, §4) — models risk management and lets the tail show as a (capped-ish)
  loss. A true gap can still exceed this; that is the point.
- **Sizing:** `LOT_SIZE = 75` (NIFTY), **1 lot** per leg; `CAPITAL = ₹1,000,000`
  paper book (covers strangle margin). One position at a time.

## 4. Costs — the crux, deliberately HARSH (where most backtests cheat)

- **Spread haircut = `SPREAD_PCT = 10%` of each leg's model price, per
  transaction.** Charged on **entry** (reduces premium collected) and again on any
  **early stop-out exit**. This stands in for the wide NSE option bid-ask; it is
  set intentionally punitive because spread is exactly where the edge dies, and we
  would rather kill a marginal strategy than ship a flattered one.
- Plus the standard intraday-style statutory costs on premium turnover (brokerage
  ₹20/leg cap, STT on sell premium, exchange txn, GST) — secondary to the spread.

## 5. Verdict rule (no premature conclusions)

- The report stays **"INCONCLUSIVE — awaiting a volatility event"** until the
  forward book has held a position through at least one genuine vol event:
  a session with `|NIFTY daily move| ≥ 4%`, OR a flagged budget/major-event day,
  while short. "Survives quiet months" is explicitly **not** evidence.
- There is no benchmark pass/fail gate (this is exploratory). The honest measure:
  after the harsh spread, does cumulative P&L stay positive **through** the vol
  event, not just around it.

## 6. Ledger & autonomy

- Separate SQLite **`options.db`** (own `account`, `positions`/`legs`, `cycles`).
  Never touches `portfolio.db` or `intraday.db`.
- Runs daily unattended (open new cycle if flat; mark-to-model; settle at expiry;
  honor stop) — pure simulation, places **no orders**. If this ever earned the
  right to go live, it would be **semi-automatic: bot proposes, human approves** —
  never fully autonomous (see [[options-discipline]] / THESIS §why-it-dies-2).
