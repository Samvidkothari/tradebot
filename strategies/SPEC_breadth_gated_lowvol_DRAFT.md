# SPEC — Breadth-Gated Low-Vol Exposure  *(DRAFT)*

**Pre-registered 2026-07-12, BEFORE any breadth-gate backtest was run.** No
parameter in this file may change after seeing results (Phase 2B Rule 3). One
parameter set per thesis. If it fails the pass criteria, the thesis failed.

Status: **DRAFT → REJECTED** (2026-07-12 gauntlet). Not wired into
`paper_trader.py`, not in the live REGISTRY, does not modify any pre-registered
file (`lowvol.py` and `regime_overlay.py` untouched; the base signal is
*imported read-only*). Long-only, long-or-cash, daily bars, NIFTY-50 cash.
Paper only; no order code.

Origin: Loop-2 regime/overlay scout, 2026-07-12, following the 7/09 memo's
"low-turnover overlay on the low-vol sleeve" recommendation. Full argument in
`THESIS_breadth_gated_lowvol_DRAFT.md`.

## 1. Universe & data
Base strategy: the pre-registered low-vol rules exactly (`lowvol.target_portfolio`,
60d vol, 15 lowest, equal-weight, monthly), used unchanged. Breadth measured on
the same 48-name close panel (`data_io.load_panel`), ffill'd closes.
`NIFTY50.csv` is the benchmark only. No leverage, no shorting → no unbounded tail.

## 2. Locked parameters

| Parameter | Value | Meaning |
|---|---|---|
| `BREADTH_SMA` | **200** | per-name simple moving average window (ffill'd closes) |
| breadth *b* | fraction of names with close > 200d SMA | names need ≥200 bars to count |
| gate | **b ≥ 0.50 → 1.00 · 0.35 ≤ b < 0.50 → 0.70 · b < 0.35 → 0.40** | exposure factor, graded & monotone |
| `WARMUP` | **200** | first rebalance once breadth is computable (un-gated replica uses the same start for comparability) |
| evaluation | **at monthly rebalance only** | no intra-month gating; no new trade days |
| `TOP_N` etc. | inherited from SPEC_lowvol | base selection untouched |

Invariant (varma-style): factor ∈ {0.40, 0.70, 1.00}, monotone in breadth,
never > 1.0 (the gate can only de-risk, never lever).

## 3. Mechanical rules (objective, no look-ahead)
1. At each monthly rebalance day *t* (pos ≥ `WARMUP`): compute per-name 200d SMA
   on ffill'd closes through *t*; breadth *b* = share of eligible names above.
2. Map *b* → factor *f* via the locked gate.
3. Base target = `lowvol.target_portfolio(panel, t)` (unchanged). Invest
   *f* × (post-cost equity) equally across the 15 names; hold 1−*f* in cash
   (0% return). Buy-and-hold drift to the next monthly rebalance.
4. Factor changes trade only the exposure delta at that rebalance.

## 4. Costs
Turnover-aware, identical to the pre-registered backtests
(`config.COST_ENTRY/COST_EXIT`, two-pass). Stress leg re-runs at **1.5×**.

## 5. Pass criteria (after costs; default reject)
Formal charter verdict — PASS only if ALL hold:
1. Beat NIFTY-50 buy-and-hold CAGR in **FULL and** OOS (≥ `config.SPLIT_DATE`).
2. Max drawdown **no worse than** NIFTY's (FULL period).
3. **OOS Sharpe ≥ 1.10** (multiple-testing haircut = 0.8 + 0.05 × 6 prior ideas
   this quarter — the five before this run + lowvol_momentum_blend logged first
   today — computed and locked before testing).
4. Survives the **1.5× cost stress** (criteria 1–3 still hold).

Secondary overlay-merit record (for the human reviewer ONLY; cannot flip a
REJECT): vs the un-gated replica on OOS — ΔmaxDD, CAGR retention, and
return/|maxDD|. Recorded per the varma_riskstate precedent.

## 6. Result (2026-07-12, locked run)
**REJECTED.** Gated OOS: CAGR 3.8% (3.3% @1.5×) fails to beat index 3.9%
✗ crit-1; OOS Sharpe 0.39 (0.34 @1.5×) << 1.10 ✗ crit-3; maxDD −15.3% ✓ crit-2.
Secondary overlay-merit record (vs un-gated replica, OOS): bought **0.6pt** of
max-DD (−15.3% vs −15.9%) at the cost of **half the CAGR** (3.8% vs 7.3%);
return/|maxDD| worsened 0.46 → 0.25 — the secondary evidence is ALSO negative,
so this is a clean kill, not a bar artefact. Gate de-risked at 11/51 rebalances
(mid-2022, spring-2023, Jan–May 2025, Apr+Jul 2026) — mistimed insurance in
this tape. Contrast: the varma governor cut ~6pts of DD for ~20% of CAGR.
Breadth-at-monthly-frequency on 48 mega-caps is a lagging regime tag here.
No re-tuning.
