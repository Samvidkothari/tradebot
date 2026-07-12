# SPEC — Episodic Pivot (Bonde) × Varma governor

**Pre-registered 2026-07-08, BEFORE any EP backtest was run.** No parameter in
this file may change after seeing results (Phase 2B Rule 3). One parameter set
per thesis. If it fails the pass criteria, the thesis failed.

Status: **CANDIDATE**. Not wired into `paper_trader.py`. Long-only, daily bars,
NIFTY-50 cash. Paper only; no order code.

Origin: Pradeep Bonde's Episodic Pivots playbook (offense) combined with the
Varma risk-state doctrine already in this repo (defense). See PLAYBOOK.md for the
4-layer architecture.

---

## 1. Thesis (short)

A sharp, volume-backed thrust to a fresh high marks a stock "in play"; it
continues often enough to pay after costs *if* traded only in a supportive
regime, sized for fat tails, and exited into strength. Full argument in
`THESIS_episodic_pivot.md`.

## 2. Honest scope / known handicap

Bonde's EP requires a **fundamental catalyst** (earnings, news, guidance). This
repo has **no news/earnings feed and intraday is frozen**, so this spec tests the
**technical ignition only** — the catalyst-blind proxy. It is expected to
underperform a true catalyst-gated EP; a null result is partly evidence about the
missing data. This limitation is pre-registered, not discovered after the fact.

## 3. Universe & data
NIFTY-50 cash names (`data/*.csv`, daily OHLCV), survivorship-biased (current
members), 2021-06 → present. Daily bars only. Long only.

## 4. Mechanical rules (objective, no look-ahead)

**Layer 1 — Ignition (`episodic_pivot.py`).** On day *t*, using data through *t*:
- Relative volume: `volume[t] ≥ RVOL_MULT=2.5 × mean(volume, prior VOL_LOOKBACK=50)`
- Thrust: `close[t]/close[t-1] − 1 ≥ THRUST_MIN=0.05`
- Fresh breakout: `close[t]` is the highest close of the last `HIGH_LOOKBACK=60`
- Enter at the **next day's open** (t+1). Long only.

**Layer 4 — Sell into strength (exit).**
- Initial stop = the ignition bar's **low**; `risk = (entry − stop)/entry`.
- Take **half** off at `entry × (1 + TP1=0.15)`; move remainder stop to breakeven.
- Trail remainder with a chandelier stop: `highest_high − TRAIL_ATR=3.0 × ATR(14)`.
- Force-exit after `MAX_HOLD=60` bars. Stop checked before target (conservative).
- Blended `gross_ret = 0.5·TP1 + 0.5·(exit/entry−1)` when the half was booked.

**Layer 2 — Varma regime gate (`backtest_episodic_pivot.py`).** Drop an entry
whose NIFTY state on the entry day is a "nothing works" tape:
- `regime.classify` character = **mean_reverting**, OR
- **stress** = trend `bear` AND `vol_percentile_1y ≥ 0.85`.
Otherwise keep. Fail-safe (insufficient history): keep.

**Layer 3 — Varma sizing.** Scale each surviving entry's equity risk by the
graded exposure factor `varma_riskstate.exposure_factor` (∈ [0.40, 1.00]) as of
the entry day. Base risk = **1%** equity per trade, before the factor. Fail-safe
factor = 0.75.

## 5. Costs
Every round trip pays `config.COST_ROUNDTRIP` (≈0.32%). No free fills.

## 6. Pass criteria (judged on the COMBINED gate+size system, after costs)
PASS only if ALL hold:
1. Positive net expectancy per trade (mean R after costs > 0).
2. Profit factor ≥ 1.3.
3. Holds out-of-sample: mean R after costs > 0 on trades closing on/after
   `SPLIT_DATE` (2024-01-01).
4. Sample ≥ 100 trades (else **INCONCLUSIVE**, not PASS).

Anything else = FAIL. The backtest additionally reports RAW vs +GATE vs
+GATE+SIZE so each playbook's contribution is auditable, but the verdict is on the
combined system. Judged vs the null that a catalyst-blind mechanical breakout has
no durable, cost-surviving edge on large-cap daily bars.

## 7. Locked parameters (summary)

| Param | Value | | Param | Value |
|---|---|---|---|---|
| `VOL_LOOKBACK` | 50 | | `TP1` | 0.15 |
| `RVOL_MULT` | 2.5 | | `TRAIL_ATR` | 3.0 |
| `THRUST_MIN` | 0.05 | | `MAX_HOLD` | 60 |
| `HIGH_LOOKBACK` | 60 | | `RISK_PER_TRADE` | 0.01 |
| `ATR_N` | 14 | | stress vol pctl | 0.85 |
