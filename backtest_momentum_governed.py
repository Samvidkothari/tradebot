"""backtest_momentum_governed.py — momentum SELECTION governed by the Varma
risk-state sizer (SPEC_momentum_governed.md).

The blueprint's highest-ROI fix: cross-sectional momentum is a real edge
(+CAGR) that was set aside for a deep drawdown. That is a *sizing* problem, not a
*signal* problem. This backtest keeps the pre-registered momentum selection
BYTE-FOR-BYTE (it imports `momentum.target_portfolio` and reuses the canonical
`run_momentum` for the ungoverned baseline) and layers the graded, fractional-
Kelly `varma_riskstate` exposure factor on top: each rebalance, gross exposure is
scaled to the current market risk state (≤ 1.0, never more), the rest sits in cash.

It reports UNGOVERNED vs GOVERNED vs NIFTY so the governor's effect is explicit,
and judges the governed book against pre-registered criteria whose whole point is:
**cut the drawdown without gutting the CAGR** (better Calmar / Sharpe).

Does NOT modify `momentum.py`, `backtest_momentum.py`, or any committed verdict.
READ-ONLY / research: reads data/*.csv, writes results/momentum_governed_report.md,
places nothing.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import bnh_metrics, _p
from config import COST_ENTRY, COST_EXIT, COST_ROUNDTRIP, SPLIT_DATE
from momentum import target_portfolio, LOOKBACK, TOP_N
from backtest_momentum import run_momentum, rebalance_dates, equity_metrics
from data_io import load_panel, load_nifty
from varma_riskstate import exposure_factor as varma_exposure_factor

RESULTS_DIR = Path(__file__).parent / "results"
STARTING = 1.0
TRADING_DAYS = 252


# ── NIFTY risk-state factor per rebalance date (cached) ───────────────────────

def _nifty_series() -> pd.Series:
    nf = load_nifty()
    return pd.Series(nf["close"].values, index=pd.to_datetime(nf["date"])).sort_index()


_NIFTY = _nifty_series()


@lru_cache(maxsize=4096)
def _exposure(day: pd.Timestamp) -> float:
    """Graded Varma exposure factor (∈ [0.40, 1.00]) from NIFTY risk state as of
    `day`. Fail-safe to the module's neutral default; never > 1.0."""
    s = _NIFTY[_NIFTY.index <= day]
    try:
        f = float(varma_exposure_factor(s)["factor"])
    except Exception:
        f = 0.75
    return min(1.0, max(0.0, f))


# ── Governed engine — momentum selection, Varma-scaled gross exposure ─────────

def run_momentum_governed(panel_raw: pd.DataFrame):
    """Monthly top-15 momentum, but each rebalance invests only `exposure × equity`
    (exposure from the Varma risk state), the remainder held as cash. Selection is
    the pre-registered `target_portfolio` — unchanged. Mirrors the canonical
    `run_momentum` loop exactly except for the exposure scalar and its audit trail.

    Returns (equity Series, n_changes, history, exposure_path dict)."""
    panel_val = panel_raw.ffill()
    rebals = rebalance_dates(panel_raw)
    if not rebals:
        return pd.Series(dtype=float), 0, [], {}

    first_rebal = rebals[0]
    rebal_set = set(rebals)

    shares, cash, prev_held = {}, STARTING, set()
    entries = exits = 0
    history, equity, exposure_path = [], {}, {}

    days = panel_raw.index[panel_raw.index >= first_rebal]
    for day in days:
        px = panel_val.loc[day]

        if day in rebal_set:
            pv = cash + sum(sh * px[s] for s, sh in shares.items())
            exposure = _exposure(pd.Timestamp(day))            # ≤ 1.0
            exposure_path[day] = exposure

            target = target_portfolio(panel_raw, day, top_n=TOP_N)
            names = set(shares) | set(target)
            cur_val = {s: shares.get(s, 0) * px[s] for s in names}

            # Two-pass, turnover-aware cost — identical structure to run_momentum,
            # but the target per name is scaled by `exposure` (freed capital → cash).
            invest_target = pv * exposure
            tgt_each0 = invest_target / TOP_N
            bought = sum(max((tgt_each0 if s in target else 0) - cur_val[s], 0)
                         for s in names)
            sold = sum(max(cur_val[s] - (tgt_each0 if s in target else 0), 0)
                       for s in names)
            cost = bought * COST_ENTRY + sold * COST_EXIT

            invest = (pv * exposure) - cost
            tgt_each = max(invest, 0.0) / TOP_N

            new_shares = {}
            for s in target:
                price = px[s]
                if pd.notna(price) and price > 0:
                    new_shares[s] = tgt_each / price

            new_held = set(new_shares)
            entries += len(new_held - prev_held)
            exits += len(prev_held - new_held)

            invested = tgt_each * len(new_shares)
            cash = pv - invested - cost
            shares = new_shares
            prev_held = new_held

            history.append(dict(date=day, n_holdings=len(new_shares),
                                 exposure=exposure, bought=bought, sold=sold, cost=cost))

        pv = cash + sum(sh * px[s] for s, sh in shares.items())
        equity[day] = pv

    return pd.Series(equity).sort_index(), entries + exits, history, exposure_path


# ── Metrics helpers ───────────────────────────────────────────────────────────

def _sharpe(equity: pd.Series) -> float | None:
    if equity is None or len(equity) < 3:
        return None
    r = equity.pct_change().dropna()
    sd = r.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return None
    return float(r.mean() / sd * np.sqrt(TRADING_DAYS))


def _calmar(m: dict) -> float | None:
    if not m or not m.get("max_dd"):
        return None
    return abs(m["cagr"] / m["max_dd"]) if m["max_dd"] != 0 else None


def _slice(eq, start=None, end=None):
    return equity_metrics(eq, start=start, end=end)


# ── Pre-registered pass criteria (SPEC_momentum_governed.md) ──────────────────

def evaluate(base_full, gov_full, base_b, gov_b, gov_curve, base_curve):
    # C1 — governor cuts the drawdown (its whole purpose)
    c1 = bool(base_full and gov_full and abs(gov_full["max_dd"]) < abs(base_full["max_dd"]))
    # C2 — better risk-adjusted return (Calmar) full AND out-of-sample
    bc_f, gc_f = _calmar(base_full), _calmar(gov_full)
    bc_b, gc_b = _calmar(base_b), _calmar(gov_b)
    c2 = bool(bc_f and gc_f and gc_f > bc_f and bc_b and gc_b and gc_b > bc_b)
    # C3 — retains >= 60% of ungoverned CAGR (doesn't gut the edge)
    c3 = bool(base_full and gov_full and base_full["cagr"] > 0
              and gov_full["cagr"] >= 0.60 * base_full["cagr"])
    return dict(c1=c1, c2=c2, c3=c3, passed=(c1 and c2 and c3))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_raw, nifty_df = load_panel()

    base_eq, base_changes, _ = run_momentum(panel_raw)                    # canonical
    gov_eq, gov_changes, gov_hist, expo = run_momentum_governed(panel_raw)

    base_full, gov_full = _slice(base_eq), _slice(gov_eq)
    base_b = _slice(base_eq, start=SPLIT_DATE)
    gov_b = _slice(gov_eq, start=SPLIT_DATE)
    nifty_full = bnh_metrics(nifty_df, start=base_eq.index[0])
    nifty_b = bnh_metrics(nifty_df, start=SPLIT_DATE)

    verdict = evaluate(base_full, gov_full, base_b, gov_b, gov_eq, base_eq)
    exps = list(expo.values())
    W = 96

    def line(tag, m, eq):
        if not m:
            return f"  {tag:<26} (insufficient data)"
        return (f"  {tag:<26} CAGR {_p(m['cagr'])}  MaxDD {_p(m['max_dd'])}  "
                f"Calmar {(_calmar(m) or 0):.2f}  Sharpe {(_sharpe(eq) or 0):.2f}")

    print(f"\n{'='*W}\n  MOMENTUM (12-1) governed by VARMA risk-state sizer — pre-registered\n{'='*W}")
    print(f"  Cost/round-trip ≈{COST_ROUNDTRIP*100:.3f}%   ·   OOS split {SPLIT_DATE}   ·   "
          f"exposure {min(exps):.2f}–{max(exps):.2f} (mean {np.mean(exps):.2f})")
    print(f"  {'-'*W}")
    print(line("Ungoverned — full", base_full, base_eq))
    print(line("Governed   — full", gov_full, gov_eq))
    print(line("NIFTY B&H  — full", nifty_full, None).replace("Sharpe 0.00", "") )
    print(f"  {'-'*W}")
    print(line("Ungoverned — OOS (B)", base_b, base_eq[base_eq.index >= pd.Timestamp(SPLIT_DATE)]))
    print(line("Governed   — OOS (B)", gov_b, gov_eq[gov_eq.index >= pd.Timestamp(SPLIT_DATE)]))
    print(f"  {'-'*W}")
    print(f"    [{'PASS' if verdict['c1'] else 'FAIL'}] 1. Governed max drawdown smaller than ungoverned")
    print(f"    [{'PASS' if verdict['c2'] else 'FAIL'}] 2. Better Calmar (CAGR/|DD|) full AND out-of-sample")
    print(f"    [{'PASS' if verdict['c3'] else 'FAIL'}] 3. Retains >= 60% of ungoverned CAGR")
    print(f"\n  VERDICT (governed sleeve): {'PASS' if verdict['passed'] else 'FAIL'}\n{'='*W}\n")

    # report
    def blk(tag, m, eq):
        if not m:
            return f"| {tag} | — | — | — | — |\n"
        return (f"| {tag} | {_p(m['cagr'])} | {_p(m['max_dd'])} | "
                f"{(_calmar(m) or 0):.2f} | {(_sharpe(eq) or 0):.2f} |\n")
    oos_base_eq = base_eq[base_eq.index >= pd.Timestamp(SPLIT_DATE)]
    oos_gov_eq = gov_eq[gov_eq.index >= pd.Timestamp(SPLIT_DATE)]
    md = [
        "# Momentum × Varma governor — Backtest Report\n\n",
        f"Generated: {date.today()}  \n",
        "Spec: `strategies/SPEC_momentum_governed.md` (pre-registered).  \n",
        "Selection = pre-registered `momentum.target_portfolio` (unchanged). "
        "Overlay = graded fractional-Kelly `varma_riskstate` exposure factor, "
        "applied to gross exposure each rebalance (≤ 1.0, freed capital → cash).  \n",
        f"Costs ≈{COST_ROUNDTRIP*100:.3f}% round-trip on turnover. OOS split {SPLIT_DATE}. "
        f"Exposure ranged {min(exps):.2f}–{max(exps):.2f} (mean {np.mean(exps):.2f}).\n\n",
        f"## Verdict: **{'PASS' if verdict['passed'] else 'FAIL'}**\n\n",
        "| Book (full period) | CAGR | Max DD | Calmar | Sharpe |\n|---|---|---|---|---|\n",
        blk("Ungoverned momentum", base_full, base_eq),
        blk("Governed momentum", gov_full, gov_eq),
        blk("NIFTY 50 B&H", nifty_full, None),
        "\n| Book (out-of-sample, ≥%s) | CAGR | Max DD | Calmar | Sharpe |\n|---|---|---|---|---|\n" % SPLIT_DATE[:7],
        blk("Ungoverned momentum", base_b, oos_base_eq),
        blk("Governed momentum", gov_b, oos_gov_eq),
        blk("NIFTY 50 B&H", nifty_b, None),
        "\n### Pre-committed pass criteria\n\n",
        f"- {'✅' if verdict['c1'] else '❌'} 1. Governed max drawdown smaller than ungoverned (the governor's purpose)\n",
        f"- {'✅' if verdict['c2'] else '❌'} 2. Better Calmar (CAGR/|MaxDD|) in full period AND out-of-sample\n",
        f"- {'✅' if verdict['c3'] else '❌'} 3. Retains ≥ 60% of ungoverned CAGR (edge not gutted)\n",
        "\n### How to read this\n\n",
        "The signal is identical to the pre-registered momentum sleeve; only the "
        "*size* changes with the market's risk state. A working governor trades a "
        "little CAGR for a materially smaller drawdown — a higher Calmar/Sharpe. "
        "If it fails these criteria the overlay fails; parameters are locked (the "
        "Varma sizer's) and not tuned to this result. Default verdict is reject; "
        "human-only promotion.\n",
    ]
    (RESULTS_DIR / "momentum_governed_report.md").write_text("".join(md))
    print(f"  Report saved → results/momentum_governed_report.md\n")
    return "PASS" if verdict["passed"] else "FAIL"


if __name__ == "__main__":
    main()
