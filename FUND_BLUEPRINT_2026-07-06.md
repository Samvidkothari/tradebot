# Tradebot → Multi-Strategy Fund Blueprint

*PM review, 2026-07-06. Grounded in a full code scan, the 7/04 strategy review, and the live paper ledgers.*

---

## 0. The uncomfortable truth first

There is no money-printing machine, and your own ledger is the proof: the intraday books had a **gross edge of +₹11,405 that costs turned into −₹21,509 net**. Anyone promising a printer is selling one. What a 25-year desk actually builds is a **multi-sleeve compounder**: several small, uncorrelated, cost-surviving edges, risk-budgeted together, run with discipline. On a NIFTY-50 daily-bar universe at moderate risk, an honest target is:

| Metric | Realistic target | "Printer" fantasy |
|---|---|---|
| CAGR | 12–16% (vs NIFTY ~7–8%) | 50%+ |
| Sharpe | 0.9–1.3 | 3+ |
| Max drawdown | −10 to −14% budget | none |
| Win rate on new strategy ideas | ~1 in 5 survives validation | all |

Your methodology (pre-registration, one parameter set per thesis, after-cost OOS verdicts) is already institutional-grade — better than most retail quant shops. The gap is not rigor; it's **breadth** (one live alpha) and **portfolio construction** (no risk-budgeting layer across books). This blueprint fixes both.

---

## 1. What the scan found

**Assets you already have** (this is a real research stack, use it):
- One **proven sleeve**: low-vol anomaly (+11.1% vs +6.9% CAGR, OOS +7.6% vs +4.2%, DD −15.9% better than index) with a pre-registered defensive `regime_overlay.py`.
- A **16-factor library** + `multifactor.py` ranker + `feature_store.py` cache — an alpha factory with no strategies promoted out of it yet.
- `regime.py` (trend/vol/character classifier), `portfolio_optimizer.py` (constraint-aware allocation), `risk_engine.py` (limits + emergency flag), `research_pipeline.py` (daily orchestrator), `research_assistant.py` (deterministic reviewer).
- Clean layering, acyclic imports, read-only ledger guards, 40+ test files, smoke harness.

**Gaps that cap your P&L:**
1. **Single-alpha concentration** — one live equity sleeve; momentum (a real edge, +14.7% CAGR) was discarded for its −27% DD instead of being *fixed* with sizing.
2. **No cross-book risk budgeting** — four independent ₹10L books, nobody owns portfolio-level heat, correlation, or drawdown.
3. **Alpha factory idles** — `factor_models.json` has one model (`core4`) and it's explicitly "research only." Nothing graduates.
4. **Options verdict blocked** on a ≥4% NIFTY day with no pre-committed judgment criteria (open item #7 in your 7/04 review — close it).
5. **No autonomous multi-sleeve trader** — `paper_trader.py` runs one book; there's no meta-layer sizing sleeves against each other.

---

## 2. Portfolio design — the sleeve architecture

One ₹10L **master book**, risk-budgeted across sleeves (not four disconnected ₹10L books). Risk budget = share of a **10–12% annualized portfolio vol target**, not share of capital.

| Sleeve | Strategy | Status | Risk budget | Why |
|---|---|---|---|---|
| A — Core | Low-vol top-15, monthly, + regime overlay | ✅ live paper | **45%** | Proven after costs, OOS, and in DD. The anchor. |
| B — Trend | **Vol-targeted momentum** (see below) | needs pre-registration | **25%** | The +14.7% CAGR edge is real; the −27% DD is a *sizing* failure, not a signal failure. |
| C — Carry | Iron condor ladder (defined-risk VRP) | 🟡 audition running | **15%** | Uncorrelated to equity sleeves; tail is capped at ₹30,734/cycle. Retire the naked strangle whatever the verdict — unlimited tail has no place at moderate risk. |
| D — Blend | One locked `core4`-style multi-factor model | needs pre-registration | **15%** | Graduates the idle factor library into a sleeve. One weight set, committed before backtest, or it doesn't run. |
| — | Intraday anything | ⚰️ stays dead | 0% | Cost lesson is frozen evidence. Do not reopen. |

**Sleeve B spec (the highest-ROI fix in this document):** same 12-1 momentum signal, but position size scaled by `target_vol / realized_vol` per name (ATR sizing already exists in `risk_engine.py`), a portfolio-level 15% vol cap, and the same regime overlay gate as low-vol (halve exposure in bear+high-vol). Pre-register it as `SPEC_momentum_voltarget.md` with locked parameters. Expected effect based on the literature and your own numbers: keep most of the CAGR, cut the DD by a third to a half. If it fails the same three pre-committed criteria low-vol passed, it stays dead — but you owe the thesis one properly-sized audition.

**Portfolio-level controls (new, sits above all sleeves):**
- **Vol targeting:** scale gross exposure daily so trailing 20d portfolio vol ≤ target; de-risk to cash above it (`portfolio_optimizer.py` already has the heat logic — promote it from lens to law).
- **Drawdown circuit breaker:** −8% from high-water mark → all sleeves to half size; −12% → new entries halted, `risk_engine` emergency flag raised. Pre-commit these numbers.
- **Correlation cap:** if 60d correlation between sleeves A and B exceeds 0.8, the smaller sleeve's budget shrinks (both are long NIFTY names; the overlay is what keeps them distinct).
- **Per-day trade throttle** for the autonomous bot (see §4).

---

## 3. The multi-agent research pipeline

You asked for multiple agent layers. Map them onto what exists — each agent is a role with a contract, implemented as a Claude subagent/scheduled task orchestrating your existing modules. The pipeline is a funnel: **many ideas in, one or two pre-registered strategies out per quarter.**

```
 L1  DATA SENTINEL      data_quality + fetch_data          "is the data clean & fresh?"
 L2  ALPHA SCOUTS (×3)  factors/feature_store/market_intel  generate candidate theses
       · cross-sectional scout  (factor library sweeps, decay checks)
       · overlay scout          (regime-conditional sizing/exposure ideas)
       · vol/derivatives scout  (VRP structures, expiry calendar effects)
 L3  ADVERSARIAL VALIDATOR  backtests + tearsheet + walk-forward + Monte Carlo
       the red team: tries to KILL every L2 idea before it costs money
 L4  RISK MANAGER       risk_engine + risk_analytics        limits, heat, correlation, veto power
 L5  ALLOCATOR (CIO)    portfolio_optimizer                 risk budgets across surviving sleeves
 L6  EXECUTION BOT      meta paper trader (§4)              autonomous multi-sleeve paper trades
 L7  HISTORIAN          digest + research_assistant         daily digest, weekly PM memo, decay alerts
```

**Rules that make the layers worth having:**
- **Scouts propose, never test.** A scout's output is a written thesis + spec (your existing `strategies/THESIS_*.md` format). This preserves pre-registration — the entity that generates an idea never grades it.
- **The Validator is adversarial by charter.** It runs the OOS split, walk-forward, Monte Carlo, then adds two things you don't do yet: a **cost stress at 1.5× the config cost model** (would intraday have died sooner? yes — institutionalize that lesson) and a **multiple-testing haircut**: track how many ideas were tried this quarter and require higher OOS Sharpe the more were tested (a deflated-Sharpe discipline). Default verdict is *reject*.
- **The Risk Manager holds a veto** independent of returns: any strategy with an unbounded tail (naked strangle), turnover its cost-stress can't survive, or >0.8 correlation to an existing sleeve is rejected regardless of backtest.
- **The Allocator only sees survivors** and only chooses budgets, never parameters.
- **Cadence:** L1 daily pre-open · L2 weekly · L3 on-demand per thesis · L4 daily post-close · L5 monthly (rebalance day) · L6 daily · L7 daily digest + Friday memo. Your existing `research_pipeline.py` is the daily spine; the scheduled-task digest you already run is L7's first half.
- **Throughput expectation:** ~10–15 theses/quarter from L2, 2–3 reach a full L3 audition, 0–1 gets budget. That ratio is what a healthy desk looks like; anything richer means the Validator has gone soft.

---

## 4. The autonomous multi-trade bot (paper)

A `meta_trader.py` above the existing sims — the single writer for the master book:

1. **Inputs:** sleeve target portfolios (from each strategy's `select()` via the existing `REGISTRY`), regime tags, risk-engine status, allocator budgets.
2. **Sizing:** sleeve budget × vol-target scalar × regime-overlay factor → per-name quantities.
3. **Guardrails (hard-coded, not config):** max 20 orders/day; max 8% single name; no entries while the emergency flag is up; every fill is a SQLite row with the full decision context (sleeve, signal value, regime tag, risk state) so the Historian can attribute P&L to decisions, not just positions.
4. **Kill switch:** one flag file; bot checks it before every action. Human sets it, only a human clears it.

**On live money — the part a 25-year manager owes you straight:** your repo deliberately contains no order-placement code, and that gate is correct. I have not added any. Keep the gate until the *master book* (not one sleeve) shows **≥6 months of paper track record** meeting three pre-committed criteria: portfolio Sharpe > 0.9, max DD inside the −12% budget, and realized costs within 20% of the modeled costs. If you go live after that, go at 10% of intended capital for a quarter first, and place the arm/disarm switch and the capital decision with the human, never the bot. This is engineering design, not investment advice — what to risk is your call, ideally with a real advisor.

---

## 5. 90-day roadmap

| Weeks | Deliverable | Layer |
|---|---|---|
| 1–2 | Pre-commit options judgment criteria (closes open item #7); pre-register `SPEC_momentum_voltarget.md`; write the drawdown/vol-target numbers into `risk_limits.json` as pre-committed portfolio law | L3/L4 |
| 3–4 | Momentum-voltarget audition through the full Validator gauntlet (OOS + walk-forward + MC + 1.5× cost stress) | L3 |
| 5–6 | `meta_trader.py` master book + guardrails + tests (mirror `test_security_boundaries.py` discipline); sleeves A(+B if passed) live in paper | L6 |
| 7–8 | Scout charters as agent prompts; first weekly thesis cycle; multiple-testing register (`results/thesis_register.json`) | L2 |
| 9–10 | Condor ladder: 2–3 overlapping monthly cycles instead of one, if the vol-event verdict lands GREEN; strangle retired either way | Sleeve C |
| 11–12 | Pre-register one multi-factor model (locked weights) → audition → allocate or kill; first full L1–L7 week runs unattended | Sleeve D |
| ongoing | Historian: daily digest (already scheduled) + Friday PM memo with decay alerts from `research_assistant.py` | L7 |

---

## 6. What I will not recommend

Intraday revival (your own frozen evidence), naked short options (unlimited tail ≠ moderate risk), result-driven re-tuning of pre-registered specs (the moment you allow it, every verdict you've earned becomes noise), leverage before the master book has a track record, and any belief that more agents means more alpha — the agents exist to *kill bad ideas faster*, which is the only reliable way research compounds.

*All simulated/paper. Nothing here is financial advice; capital and go-live decisions stay with you.*
