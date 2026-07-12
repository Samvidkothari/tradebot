# Futures Trend-Following — Build Scope (design only, no code yet)

*Drafted 2026-07-08. A scoping/planning document for a Varma-aligned managed-
futures sleeve, to be built in phases under the existing governance
(`SELF_IMPROVE.md`: pre-registration, one locked parameter set, after-cost
verdicts, default reject, human-only promotion). Nothing here places orders. This
is the "should we, and how" — the THESIS/SPEC come later, before any backtest.*

---

## 0. Why this sleeve (the case)

Time-series (trend) following on futures is the **most philosophically aligned**
move available to this repo: Dr. Varma is a systematic futures trend-follower, and
Bonde's "duration moves" are the same idea. It is attractive here for three
concrete reasons the equity sleeves cannot offer:

1. **Genuine diversification.** A trend sleeve can go **long or short** across
   **uncorrelated markets** (equity index, commodities, currency, rates). That is
   the negatively-correlated return stream the blueprint and Varma both say is
   rare and precious — the equity sleeves (low-vol, momentum) are all long-NIFTY
   and move together.
2. **Cost-friendly.** Trend is **low-frequency** (weeks-to-months holds), so the
   cost wall that killed intraday and drags the turn-of-month idea does not bite.
3. **Crisis behavior.** Diversified trend historically has a **long-volatility,
   positive-skew** payoff — it tends to make money in sustained sell-offs (short
   legs) precisely when the long-only sleeves bleed. This is the "crisis alpha"
   the portfolio currently lacks.

The honest counterweight: trend is a **crowded, decayed** edge with **long, deep
drawdowns** and multi-year flat stretches; it demands patience and broad market
breadth. On a *single* market (just NIFTY futures) it is weak — the edge lives in
**diversification across many markets**, which is exactly where our data is
thinnest (see §2). So the scoping question is really a **data** question.

---

## 1. The strategy (what we'd actually pre-register)

A classic, defensible, non-overfit design — locked before any backtest:

- **Signal:** time-series momentum. For each market, go long if the trend is up,
  short if down, flat if ambiguous. Canonical, transparent rules only, e.g. price
  vs a long moving average + the sign of 12-month return (skip most recent month),
  or a slow/medium EMA cross. **One** rule set, no per-market tuning.
- **Universe:** as many liquid, uncorrelated futures as we can get clean data for
  (see §2). More markets = the edge; a 1–2 market version is not worth building.
- **Sizing:** **volatility targeting** per market (position ∝ target_vol /
  realized_vol, using ATR — already in `risk_engine.py`), equal risk per market,
  then the **`varma_riskstate` governor** on top for portfolio gross exposure.
  Fractional-Kelly, never levered beyond a pre-set cap.
- **Exit:** opposite signal or a volatility/chandelier trailing stop (reuse the
  sell-into-strength machinery from `episodic_pivot.py`).
- **Risk:** per-market risk cap, portfolio vol target (10–12%, per blueprint),
  correlation-aware gross cap, and the blueprint's drawdown circuit breaker.

This reuses almost everything we already have: `regime.py`, `varma_riskstate.py`,
`risk_engine.py`, `portfolio_optimizer.py`, the strategy-plugin contract, the
backtest/verdict harness. The **new** work is data and instrument mechanics.

---

## 2. Data & instrument mechanics — the real work (and the blocker)

This is where the effort and the risk are. Futures are not stocks:

- **Continuous back-adjusted series.** Futures expire; a backtest needs a single
  continuous price built by **rolling** from the front contract to the next and
  **back-adjusting** for the roll gap. Getting this right (roll dates, adjustment
  method, no look-ahead) is the single biggest correctness hazard — a naive splice
  fabricates or destroys returns. Needs its own module + tests, mirroring the care
  in `data_layer.py` / `data_version.py`.
- **Contract specs per market:** lot/multiplier, tick size, expiry convention,
  trading calendar, margin. For NSE: index futures (NIFTY, BANKNIFTY) expire last
  Thursday; commodity (MCX) and currency futures have their own calendars. Extends
  `market_intel.py` (already holds NSE holidays/expiries/sectors).
- **Roll cost & carry.** Each roll pays spread + the calendar basis; must be in
  the cost model (`config.py`) or the backtest flatters itself — the same "costs
  are the crux" discipline as the options sims.
- **Data availability — the honest gap.** The repo is on **free yfinance daily
  bars**. yfinance covers some continuous futures (US: `ES=F`, `CL=F`, `GC=F`,
  etc.) but Indian NSE/MCX futures history is **not reliably available free**. So
  realistically:
  - **Cheapest path:** build the sleeve on **US/global futures** yfinance *does*
    serve (equity index, treasuries, gold, oil, FX) — enough markets for real
    diversification, and the trend literature is built on exactly these.
  - **India-native path** (NIFTY/BANKNIFTY/MCX): needs a **paid data feed** for
    continuous contract history. Larger cost + integration.

**Recommendation:** prototype on the **global yfinance futures basket** first —
it tests the *thesis* (does diversified trend pay after costs for us?) with data
we already can get, before spending on an India futures feed.

---

## 3. Phased plan (each phase gated by the prior one)

- **Phase 0 — Data spike (1 unit).** Prove we can build ONE correct continuous,
  back-adjusted series from free data, with roll logic + tests. If we can't get
  clean multi-market futures data, **stop here** — the sleeve is data-blocked and
  that's the finding. (Deliverable: `futures_data.py` + `test_futures_data.py`,
  no strategy yet.)
- **Phase 1 — Single-market proof.** Time-series momentum on one liquid market
  (e.g. an equity-index future), after roll + trading costs, OOS split. Sanity
  only — a single market won't be great; we're validating the *plumbing*.
- **Phase 2 — Diversified sleeve (the actual thesis).** 8–15 markets across ≥3
  asset classes, equal-risk vol-targeted, `varma_riskstate` governor on top.
  Pre-register THESIS/SPEC with locked rules; judge after costs vs pre-committed
  criteria (positive OOS return, Sharpe past the multiple-testing haircut,
  drawdown within budget, low correlation to the equity sleeves).
- **Phase 3 — Paper-forward + integration.** If Phase 2 passes, run it forward on
  a paper book (own ledger, like `condor.db`), and only then consider risk-budget
  integration into the multi-sleeve portfolio. Human-promoted, never autonomous
  order placement.

---

## 4. Effort & risk summary

| Item | Effort | Risk / note |
|---|---|---|
| Continuous back-adjusted data + roll | **High** | Correctness-critical; the main hazard |
| Contract specs / calendars | Medium | Extends `market_intel.py` |
| Roll + trading cost model | Medium | "Costs are the crux" — must be honest |
| Signal + sizing + risk | **Low** | Reuses regime/varma/risk_engine/plugin harness |
| India-native futures data | High + **₹ cost** | Needs a paid feed; defer past the global prototype |

**Bottom line:** the strategy code is the easy 20%; **data engineering is the
80%**, and Indian-futures data is a paid dependency. Highest-expected-value path:
a **global-futures trend prototype on free data** to settle the thesis cheaply,
then decide on an India feed only if the prototype earns it. This is the serious
long-term expansion — treat it as a multi-week project with a hard Phase-0 gate,
not a quick add.

---

## 5. Fit with the existing fund

Slots in as a new **diversifying sleeve** alongside the blueprint's A (low-vol,
live), B (governed momentum, now a passing candidate), C (iron-condor carry). It
is the first sleeve that is **not** long-NIFTY-beta and the first that can be
**short** — so even a modest, honestly-tested trend edge would improve portfolio
Sharpe more than another long-equity variant. Governed by the same one risk brain
(`varma_riskstate`) as everything else.
