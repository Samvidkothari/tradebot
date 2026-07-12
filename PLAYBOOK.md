# PLAYBOOK — combined Bonde × Varma trading architecture

*App memory. How two interviews' doctrines fit into one governed pipeline for
this repo. Companion to `VARMA_DOCTRINE.md` (defense) and the Bonde notes below
(offense). Paper/research only — nothing here places orders. Human-promoted only.*

## The one-line idea

**Bonde is the offense; Varma is the defense.** Bonde says *what to buy and when*
(trade what's in play — a volume-backed thrust to a new high, on a catalyst).
Varma says *how much and whether* (classify the risk state, don't predict; size a
fraction of Kelly; cut in fat-tail regimes). Neither works alone: ungoverned
offense blows up on a "god-syndrome" streak; defense with no alpha just sizes an
empty book. Put them in series → a real edge with a brake bolted on.

## The 4-layer pipeline

| Layer | Owner | What it does | In this repo |
|---|---|---|---|
| 1 · Selection | **Bonde** | Pick stocks "in play": rel-volume surge + thrust + fresh high | `episodic_pivot.py` (ignition) |
| 2 · Regime gate | **Varma** | Refuse entries in a "nothing works" tape (chop / bear+high-vol) — Bonde's own off-switch, made mechanical via `regime.classify` | gate in `backtest_episodic_pivot.py` |
| 3 · Sizing | **Varma** | Scale each entry by the graded fractional-Kelly exposure factor | `varma_riskstate.py` |
| 4 · Exit | **both agree** | Sell into strength: book half into the first magnitude spike, breakeven + trail the rest | exit in `episodic_pivot.py` |

Read it as: **Bonde picks → Varma's classifier decides if the tape deserves
aggression → Varma sizes each position → a shared sell-into-strength rule takes
profit.** The Varma sizer is strategy-agnostic, so it governs *any* sleeve (it
already generalizes the live low-vol `regime_overlay`).

## Where the two doctrines literally converge

- **"Everything works / nothing works" (Bonde) = "classify, don't predict" (Varma).**
  Don't forecast the tape; label it and stand aside when it's hostile.
- **"Sell 80% into strength, ride 20%" (Bonde) = fractional-Kelly de-risking (Varma).**
  Same instinct: take profit, don't hold for the perfect top; magnitude moves
  mean-revert (Bonde) and tails are fat (Varma), so trim early.
- **"Start with 5–10 shares, scale slowly" (Bonde) = fraction-of-Kelly sizing (Varma).**
- **"God syndrome" after win streaks (Bonde)** is exactly what a risk governor
  (Varma) exists to contain.

## Bonde doctrine (offense) — distilled

- **Self-leadership** is the #1 separator: solve your own problems, stay
  motivated through drawdowns, believe in multi-year eventual success.
- **Trade where the money is.** Most edge is "singles" (consistent small wins),
  not home runs. News/catalyst plays are 80–90% of winning day-trading.
- **Charts alone are not setups** — a move needs a *fundamental reason* (sector/
  theme strength, earnings, news). This is the crux (see handicap below).
- **Copy a proven playbook exactly** before innovating; master **one setup** for
  a long time; higher trade frequency → faster learning.
- **Magnitude vs duration moves:** fast +100–200% spikes mean-revert (don't
  hold); slow sustained growth persists (hold, with the right setup).
- **Macro/Fed dominates;** don't fight liquidity. Recognize secular regimes.
- **Be skeptical; verify with deep-dive/backtest.** Execution, not ideas, pays.
- **Losing-streak 4-factor check:** Setup · Process · Market · Trader — diagnose
  which broke before adjusting.
- **Bonde's 3 sectors over 25y:** Technology, Biotech/Healthcare, Consumer
  Discretionary. **Top-3 tips:** define your timeframe · deep-dive research ·
  be process-oriented.

## Honest handicap (do not forget this)

Bonde's EP **requires a fundamental catalyst**. This repo has **no news/earnings
feed and intraday is frozen** (the cost lesson). So `episodic_pivot.py` tests the
**technical ignition only** — catalyst-blind — and is pre-registered as expected
to underperform a true EP. First backtest (2026-07-08) confirmed it: **no
cost-surviving edge** on large-cap daily bars (raw exp −0.12R / PF 0.79; gated+
sized −0.42R; INCONCLUSIVE, <100 trades after gating). That is the *correct*
result — it agrees with Bonde's own rule that a chart with no catalyst is not a
setup, and the governance defaulted to reject rather than tuning to a pass.

**What would unlock the real thing:** a catalyst source (earnings calendar +
news/guidance feed) to gate ignitions, and faster-than-daily data. Until then the
EP sleeve stays a flagged candidate; the *transferable* wins are Layers 2–4 (the
Varma gate, sizing, and sell-into-strength exit), which apply to any sleeve.

## Status / governance

- `varma_riskstate` — CANDIDATE, running in **shadow** on the live paper book
  (logged `last_varma_riskstate`, shown on the Paper Trader dashboard). Not sizing
  real trades.
- `episodic_pivot` (× Varma) — CANDIDATE, INCONCLUSIVE, **not wired live**.
- `futures_trend_phase1` — CANDIDATE. First short-capable, non-NIFTY-beta sleeve
  (the intended diversifier). Engine (`futures_data.py` back-adjust, Phase 0
  PROVEN), signal + governor + costs (`futures_trend.py` /
  `backtest_futures_trend.py`) all built and tested; plumbing validated on a proxy
  in-sandbox. **Real verdict pending** `python backtest_futures_trend.py --yahoo`
  on a networked machine. Do not fund a paid data feed until this free-data
  prototype earns it.
- `momentum_governed` — CANDIDATE, **PASS** (2026-07-08). The transferable win:
  applying Layers 2–3 (Varma gate/sizing) to an edge that *exists* (momentum) cut
  the drawdown −27.2% → −21.2% while keeping ~80% of CAGR. Risk-adjusted gain is
  marginal and thin out-of-sample, so the robust effect is **smaller left tail**,
  not more return. Needs a forward shadow period before promotion. Not wired live.
- Both follow `SELF_IMPROVE.md`: pre-registered, one locked parameter set,
  after-cost/forward verdicts, default reject, **human-only promotion**. No
  automated run may promote, fund, or add order code.
