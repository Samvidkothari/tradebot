# SPEC — Futures Trend Sleeve (Phase 1 prototype)

**Pre-registered 2026-07-08.** Pass criteria committed in
`backtest_futures_trend.main()` BEFORE any real (Yahoo) run. Parameters locked;
not tuned to results. One parameter set. Phase 1 of `FUTURES_TRENDS_SCOPE.md`.

Status: **CANDIDATE / prototype**. Not wired live. Paper/research only; no orders.

## What it is

A diversified **time-series (trend) momentum** sleeve on continuous futures — the
first sleeve that can go **short** and is **not** long-NIFTY-beta (the intended
diversifier). Signal in `futures_trend.py`; portfolio/governor/costs in
`backtest_futures_trend.py`.

- **Direction:** sign of the trailing `MOM_LOOKBACK = 252`-session return (long if
  price > a year ago, short if lower).
- **Size:** volatility target per market, `weight = TARGET_VOL / realized_vol`,
  `TARGET_VOL = 0.15`, `VOL_WINDOW = 60`, capped at `WEIGHT_CAP = 2.0`.
- **Portfolio:** equal-risk average across active markets.
- **Governor (Varma-aligned):** scale gross so trailing portfolio vol ≤
  `PORT_TARGET_VOL = 0.12`, **capped at 1.0** (never lever up). NOTE the
  NIFTY-based `varma_riskstate` sizer is equity-specific, so a self-contained
  portfolio vol-target is the correct governor for a global futures book.
- **Costs:** `COST_BPS = 0.0004` per unit turnover + `ROLL_COST_BPS = 0.0002`
  charged monthly (calendar-roll proxy). Entry is next-day (shift 1 — no look-ahead).

## Data (honest, per Phase 0)

- **`--yahoo`** — Yahoo pre-stitched continuous futures (`futures_data`), the real
  prototype; needs a network (run on the Mac). Prototype quality: a true
  back-adjusted sleeve needs individual contracts from a paid feed
  (`results/futures_phase0.md`).
- **`--proxy`** (default with no network) — local equity CSVs as pseudo-markets;
  **plumbing validation only, NOT a futures verdict.**

## Pass criteria (judged after costs, on `--yahoo`)

PASS only if ALL hold:
1. **OOS return positive** after costs.
2. **OOS Sharpe ≥ 0.5** (modest bar for a Phase-1 prototype; Phase 2's diversified
   version carries the multiple-testing haircut).
3. **|correlation| to the NIFTY equity book ≤ `MAX_CORR_TO_EQUITY = 0.30`** — the
   diversification value is the whole reason this sleeve exists.

Anything else = FAIL. A `--proxy` run reports **PLUMBING-OK** and is explicitly
not a verdict. Default reject; human-only promotion.

## Scope / next

Phase 1 validates the machinery and asks "is there any trend edge for us, cheaply,
on free data." A PASS motivates **Phase 2** (8–15 markets, ≥3 asset classes,
multiple-testing haircut) and only then a paid contract-level feed. A FAIL on free
data is a legitimate, money-saving stop.
