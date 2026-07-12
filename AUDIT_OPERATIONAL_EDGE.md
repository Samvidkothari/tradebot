# Structural Gap Analysis — Operational Edge Framework (Tier-3 audit)

*2026-07-08. Auditor pass of the tradebot against the 5-pillar Operational Edge
Framework. Frame chosen by the operator: **(A) harden the daily-bar research
platform now; (B) live intraday only if it clears a pre-registered cost gate.***

## Architecture reality (the lens for every verdict)

This is a **NIFTY-50 daily-bar, multi-sleeve systematic RESEARCH platform**:
paper-only, human-promoted, no live order placement by design, **no news/pre-market
feed**, and **intraday deliberately frozen** on documented cost evidence
(gross +₹11,405 → **net −₹21,509**). Pillars are judged against *this*, not against
a US-session intraday day-trading bot.

## Scorecard

| Pillar | Fit | Verdict | Gap → Deliverable |
|---|---|---|---|
| 1 · Surgical multi-condition alerts | Partial (intraday parts N/A) | **Mostly present** | Signals already multi-condition (EP ignition = rel-vol×thrust×new-high; regime gate; priceaction). ORB/VWAP/session-time are intraday → **out of scope** under frame A. Optional: a composable filter wrapper + watchlist ranker. |
| 2 · Pre-market ingestion + watchlist priority | Poor (no feed; no "pre-market" on daily bars) | **Category-limited** | No news/catalyst feed (same blocker as EP). Legit daily-bar analog = **rank the universe into High/Med/Low** pre-rebalance via existing `multifactor.py`. Not a live pre-market pipeline. |
| 3 · Performance logging + pattern isolation | **Strong** | **Real gap** | `fills` logs P&L but **no setup_type / regime / day-of-week / hold metadata**, and decay detection is only the walk-forward Sharpe loop. → **`trade_journal.py` + `pattern_isolation.py`**. |
| 4 · Advanced rule-based exits | **Strong** | **Real gap** | Each sleeve inlines its own stop (EP has a chandelier trail; priceaction a fixed stop). No **shared, tested, never-moves-backward** module. → **`trailing_exit.py`**. |
| 5 · Post-trade autopsy packet | **Strong** | **Real gap** | Have `llm_analyst.py` / `review.py` but **no standardized per-trade autopsy** (execution, slip, rule adherence, chart coords) formatted for an LLM. → **`trade_autopsy.py`**. |
| B · Intraday reopen | — | **Gated** | Frozen on cost evidence. → **`cost_gate.py` + SPEC**: a pre-registered gate any higher-frequency idea must clear *before* code is written. |

## What "Tier-3" actually means here

Your prediction layer is already institutional-grade (pre-registration, OOS/walk-
forward, cost-honest verdicts, decay loop). The Tier-3 gap is **operational
plumbing around the trades**: uniform trade metadata → automated edge-decay
isolation → reusable ratcheting exits → LLM-ready autopsies → a hard gate that
stops the intraday cost mistake from recurring. That is exactly pillars 3, 4, 5
and the B gate — built below as production modules, each pure/tested and wired to
your existing trade-dict contract (`entry/exit/side/reason/risk/gross_ret`).

## Explicitly out of scope (and why)

- **ORB / VWAP / session-time alerts, live pre-market catalyst ingestion, tick
  slippage** — all require intraday data + a live feed you've frozen/omitted for
  reasons your own ledger validates. Building them under frame A would rebuild the
  machine you retired. They live behind the **B cost gate**: clear it first.
- **Hour/minute time-block edge decay** (the "post-11:30 AM ET" test) — N/A on
  daily bars. The daily-bar equivalents — **day-of-week, regime, setup-type, and
  recency** decay — are implemented instead, and are the honest version of the
  same idea.
