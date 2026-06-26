# SPEC — Intraday VWAP Mean-Reversion (paper simulation)

**Pre-registered:** 2026-06-12, BEFORE this strategy's code was written.
**Paper-only**: every fill is a simulated row in `intraday.db` (tagged
`strategy = 'VWAP'`). NO real orders, ever. May run unattended; never touches a
broker API.

This is the **second** intraday paper strategy, run by the same engine as the
Opening-Range Breakout (`SPEC_intraday.md`). It is a monitoring sandbox, not a
Phase 2B strategy-class audition — no pass/fail gate, no benchmark competition.

**CONCLUDED / RETIRED (2026-06-26) — sandbox finding delivered.** Over ~10 days
(49 VWAP trades): **gross +₹9,874, costs −₹15,491, net −₹5,616.** Unlike ORB,
VWAP has a real gross edge (reversion exits +₹20,105) — but it is thin enough
that costs (~₹316/trade) erase it, and trend days run the mean-reversion over:
14 end-of-day forced exits cost −₹20,215, concentrated in two trend days
(2026-06-23 −₹10,097, 06-24 −₹7,007). Same lesson as ORB: a thin intraday edge
does not survive realistic costs. Not a Phase 2B FAIL (no gate); a useful
finding, not tuned away. Daily VWAP sim **removed from `run_paper_bot.sh`**;
`intraday.db` kept as evidence.

---

## 1. What it is, in one sentence

Intraday, for the same liquid stocks: track each stock's **VWAP** (volume-
weighted average price) through the day; when price **stretches a set distance
away** from VWAP, bet it **reverts** — buy a dip below VWAP, short a stretch
above — and take profit back at VWAP. Everything is squared off by 15:15.

**Who is on the other side?** Momentum/news chasers and forced flow push price
transiently away from the day's fair-value anchor (VWAP); we provide liquidity
and are paid when it snaps back. This is the **opposite stance to ORB** (which
buys the breakout); running both shows which regime each day rewarded.

## 2. Data, universe, costs, sizing, ledger — SHARED with ORB

Identical to `SPEC_intraday.md` (so the two are directly comparable):
- yfinance 5-min bars; bar **closes** are the only fill prices (conservative).
- Same 10 liquid NSE names.
- Same intraday MIS cost model (₹20/0.03% brokerage cap, STT sell-side only, …).
- `CAPITAL = ₹1,000,000` paper, **its own book** (separate cash from ORB),
  `MAX_CONCURRENT = 5`, per-trade notional `CAPITAL / 5`, integer shares,
  longs and shorts allowed.
- Same `intraday.db`, with every row tagged `strategy='VWAP'`; idempotent per
  `(trade_date, strategy)`.

## 3. The VWAP signal

For each 5-min bar `i` of the session:
- `typical_i = (high_i + low_i + close_i) / 3`
- `VWAP_i = Σ(typical_k · volume_k) / Σ(volume_k)` for `k = 0..i` (cumulative
  from the 09:15 open; resets each day).
- `dev_i = close_i / VWAP_i − 1` (signed % distance from VWAP).

## 4. The rule (one pre-committed parameter set — not tuned to results)

- **Warm-up:** ignore the first `WARMUP_BARS = 6` bars (to ~09:45) so VWAP is
  stable before any signal.
- **Entry (first breach wins; one trade per symbol per day):** scanning bars
  from warm-up onward —
  - `dev ≤ −BAND` → enter **LONG** at that close (price stretched *below* VWAP),
  - `dev ≥ +BAND` → enter **SHORT** at that close (stretched *above* VWAP),
  - with `BAND = 0.005` (0.5%). First breach taken; that symbol is then done.
- **Target (reversion):** exit when price returns to VWAP — for a long, the
  first later bar with `dev ≥ 0`; for a short, `dev ≤ 0`. (`exit_reason="VWAP"`.)
- **Stop (stretch widens against us):** `STOP_BAND = 0.012` (1.2%). For a long,
  exit if `dev ≤ −STOP_BAND`; for a short, if `dev ≥ +STOP_BAND`.
  (`exit_reason="STOP"`.) Stop is checked before target on a bar.
- **Square-off:** any position open at the **15:15** bar is closed at that bar's
  close (`exit_reason="EOD"`). No overnight, ever.

## 5. Why these numbers (stated up front, not to be re-tuned)

`BAND = 0.5%` is a meaningful intraday stretch for liquid large-caps without
being so rare that nothing triggers; `STOP = 1.2%` gives the reversion room
(>2× the entry band) so normal wobble doesn't stop us out, while capping loss;
`WARMUP = 6 bars` keeps the first noisy half-hour from polluting VWAP. These are
the single pre-committed set — if the rule loses in simulation, that is the
finding, not an invitation to tune.

## 6. Reporting & autonomy

Same as ORB: net P&L, win rate, costs as a share of gross — now shown **per
strategy** so ORB and VWAP can be compared on identical days. Runs in the same
15:45 IST unattended job (one fetch, both strategies simulated). Pure
simulation; places no orders.
