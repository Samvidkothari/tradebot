"""backtest_episodic_pivot.py — after-cost backtest of the COMBINED Bonde+Varma
Episodic-Pivot sleeve.

This is where the two playbooks meet (see PLAYBOOK.md). It pools every mechanical
EP trade from episodic_pivot.generate_trades() across the NIFTY universe, then
layers the Varma governor on top and shows the effect stage by stage:

  RAW           — Bonde only: every ignition taken, flat 1% risk (the alpha, ungoverned)
  + GATE        — Varma "classify, don't predict": drop entries taken in a
                  "nothing works" state (choppy/mean-reverting tape, or the
                  bear+extreme-vol stress regime). Bonde's own "nothing works"
                  off-switch, made mechanical.
  + GATE + SIZE — Varma fractional-Kelly sizing: scale each surviving entry by the
                  graded risk-state exposure factor (varma_riskstate). Offense
                  picked by Bonde, throttled by Varma.

The GATE+SIZE curve is the actual combined system and is judged against the
pre-registered pass criteria in strategies/SPEC_episodic_pivot.md. READ-ONLY /
research: reads data/*.csv, writes results/episodic_pivot_report.md, places nothing.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import config
import data_io
import episodic_pivot as EP
from regime import classify, BEAR, MEANREV
from varma_riskstate import exposure_factor as varma_exposure_factor

RESULTS_DIR = Path(__file__).parent / "results"
COST = config.COST_ROUNDTRIP
SPLIT = pd.Timestamp(config.SPLIT_DATE)
RISK_PER_TRADE = 0.01     # 1% equity risk per trade (before the Varma size factor)
STRESS_VOL_PCTL = 0.85    # bear + vol at/above this pctl = stress (matches overlays)
W = 96


# ── NIFTY context for the Varma layers (cached per entry-date) ────────────────

def _nifty_series() -> pd.Series:
    nf = data_io.load_nifty()
    return pd.Series(nf["close"].values, index=pd.to_datetime(nf["date"])).sort_index()


@lru_cache(maxsize=4096)
def _gate_and_size(entry_ts: pd.Timestamp) -> tuple:
    """(gate_pass, size_factor) for an entry on `entry_ts`, from the NIFTY risk
    state as of that day. gate drops 'nothing works' tape; size is the graded
    fractional-Kelly exposure factor. Fail-safe: gate open, size neutral."""
    s = _NIFTY[_NIFTY.index <= entry_ts]
    if len(s) < 60:
        return True, 0.75                                   # too little history: neutral
    try:
        reg = classify(s)
        char = reg.get("character")
        meas = reg.get("measures") or {}
        vp = meas.get("vol_percentile_1y")
        stress = (reg.get("trend") == BEAR and vp is not None and vp >= STRESS_VOL_PCTL)
        gate = not (char == MEANREV or stress)              # Bonde "nothing works" off
    except Exception:
        gate = True
    try:
        factor = varma_exposure_factor(s)["factor"]
    except Exception:
        factor = 0.75
    return gate, float(factor)


_NIFTY = _nifty_series()


# ── Collect trades + attach the Varma layers ──────────────────────────────────

def _collect() -> pd.DataFrame:
    frames = data_io.symbol_frames(exclude_index=True)
    rows = []
    for sym, df in frames.items():
        d = df.set_index("date")
        for t in EP.generate_trades(d):
            net = t["gross_ret"] - COST
            R = net / t["risk"] if t["risk"] > 0 else 0.0
            gate, size = _gate_and_size(pd.Timestamp(t["entry_date"]))
            rows.append({"symbol": sym,
                         "entry_date": pd.Timestamp(t["entry_date"]),
                         "exit_date": pd.Timestamp(t["exit_date"]),
                         "reason": t["reason"], "R": R,
                         "gate": bool(gate), "size": float(size)})
    cols = ["symbol", "entry_date", "exit_date", "reason", "R", "gate", "size"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values("exit_date").reset_index(drop=True)


def _stats(df: pd.DataFrame, risk_col: str) -> dict:
    """Equity/expectancy stats. `risk_col` = the per-trade equity fraction risked
    (a Series aligned to df). Trades with 0 risk are effectively skipped."""
    if df.empty:
        return {"n": 0}
    R = df["R"].values
    rf = df[risk_col].values
    taken = rf > 0
    n = int(taken.sum())
    if n == 0:
        return {"n": 0}
    Rt = R[taken]
    wins, losses = Rt[Rt > 0], Rt[Rt <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    eq = np.cumprod(1 + rf[taken] * Rt)
    peak = np.maximum.accumulate(eq)
    maxdd = float((eq / peak - 1).min())
    # per-trade R Sharpe (unitless, comparable across variants)
    sharpe = float(Rt.mean() / Rt.std(ddof=1)) if len(Rt) > 1 and Rt.std(ddof=1) > 0 else 0.0
    return {"n": n, "expectancy": float(Rt.mean()), "win_rate": float((Rt > 0).mean()),
            "profit_factor": float(pf), "total_return": float(eq[-1] - 1),
            "max_dd": maxdd, "sharpe_R": sharpe}


def _variants(df: pd.DataFrame) -> dict:
    """Build the three per-trade risk columns and their stats."""
    if df.empty:
        return {k: {"n": 0} for k in ("raw", "gated", "sized")}
    df = df.copy()
    df["rf_raw"]   = RISK_PER_TRADE
    df["rf_gated"] = np.where(df["gate"], RISK_PER_TRADE, 0.0)
    df["rf_sized"] = np.where(df["gate"], RISK_PER_TRADE * df["size"], 0.0)
    return {"raw": _stats(df, "rf_raw"),
            "gated": _stats(df, "rf_gated"),
            "sized": _stats(df, "rf_sized"),
            "_df": df}


def _verdict(full: dict, oos: dict) -> tuple:
    checks = [
        ("Positive net expectancy (mean R > 0)", full.get("n") and full["expectancy"] > 0),
        ("Profit factor >= 1.3", full.get("n") and full["profit_factor"] >= 1.3),
        ("Holds out-of-sample (OOS mean R > 0)", oos.get("n", 0) and oos["expectancy"] > 0),
        (">= 100 trades", full.get("n", 0) >= 100),
    ]
    passed = all(bool(ok) for _, ok in checks)
    if full.get("n", 0) < 100:
        return "INCONCLUSIVE", checks
    return ("PASS" if passed else "FAIL"), checks


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "no trades"
    return (f"{s['n']} trades · exp {s['expectancy']:+.3f}R · win {s['win_rate']*100:.0f}% · "
            f"PF {s['profit_factor']:.2f} · total {s['total_return']*100:+.1f}% · "
            f"maxDD {s['max_dd']*100:.1f}% · Rsharpe {s['sharpe_R']:.2f}")


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    df = _collect()
    v = _variants(df)
    dff = v.get("_df", df)

    # judge the combined (gate+size) system, full + OOS
    sized_full = v["sized"]
    if not dff.empty:
        oos_df = dff[dff["exit_date"] >= SPLIT]
        sized_oos = _stats(oos_df, "rf_sized") if len(oos_df) else {"n": 0}
    else:
        sized_oos = {"n": 0}
    verdict, checks = _verdict(sized_full, sized_oos)

    print(f"\n{'='*W}\n  EPISODIC PIVOT (Bonde) × VARMA governor — after-cost backtest (pre-registered)\n{'='*W}")
    print(f"  Cost/round-trip: {COST*100:.3f}%   ·   OOS split: {config.SPLIT_DATE}")
    print(f"  RAW  (Bonde only)      : {_fmt(v['raw'])}")
    print(f"  +GATE (Varma classify) : {_fmt(v['gated'])}")
    print(f"  +GATE+SIZE (combined)  : {_fmt(v['sized'])}")
    print(f"  {'-'*W}")
    print(f"  Combined out-of-sample : {_fmt(sized_oos)}")
    print(f"  {'-'*W}")
    for name, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  VERDICT (combined system): {verdict}\n{'='*W}\n")

    # report
    def row(label, s):
        return ("| {} | — | — | — | — | — | — |".format(label) if not s.get("n") else
                f"| {label} | {s['n']} | {s['win_rate']*100:.0f}% | {s['expectancy']:+.3f} | "
                f"{s['profit_factor']:.2f} | {s['total_return']*100:+.1f}% | {s['max_dd']*100:.1f}% |")
    md = [
        "# Episodic Pivot (Bonde) × Varma governor — Backtest Report",
        f"\nGenerated: {datetime.now():%Y-%m-%d}  ",
        "Spec: `strategies/SPEC_episodic_pivot.md` (pre-registered).  ",
        "Layer 1 EP ignition (rel-vol + thrust + new high) → Layer 4 sell-into-strength "
        "exit → Layer 2 Varma regime gate → Layer 3 fractional-Kelly sizing.  ",
        f"Costs: {COST*100:.3f}% per round trip. 1% base equity risk/trade (× size factor).\n",
        "> **Catalyst-blind proxy.** Bonde's EP requires a fundamental catalyst "
        "(earnings/news) this repo cannot supply. This tests only the technical "
        "ignition and is expected to underperform a true catalyst-gated EP.\n",
        f"## Verdict (combined GATE+SIZE system): **{verdict}**\n",
        "| Variant | Trades | Win% | Expectancy (R) | Profit factor | Total | Max DD |",
        "|---|---|---|---|---|---|---|",
        row("RAW (Bonde only)", v["raw"]),
        row("+ Gate (Varma classify)", v["gated"]),
        row("+ Gate + Size (combined)", v["sized"]),
        row("Combined — out-of-sample", sized_oos),
        "\n### Pass criteria (judged on the combined system, after costs)\n",
    ]
    for name, ok in checks:
        md.append(f"- {'✅' if ok else '❌'} {name}")
    md += [
        "\n### How to read this\n",
        "Each trade's P&L is in **R** (multiples of the risk to its initial stop), "
        "net of the round-trip cost. The three rows isolate each playbook's "
        "contribution: **RAW** is Bonde's ignition alpha ungoverned; **+Gate** shows "
        "what Varma's 'classify, don't predict' removes by refusing to trade a "
        "'nothing works' tape; **+Gate+Size** adds fractional-Kelly throttling. A "
        "healthy combination should show the gate improving expectancy/drawdown "
        "even if it lowers the trade count, and sizing smoothing the drawdown "
        "further. Parameters were fixed before the run (SPEC) and are not tuned "
        "to this result. Default verdict is reject.",
    ]
    (RESULTS_DIR / "episodic_pivot_report.md").write_text("\n".join(md))
    print(f"  Report saved → results/episodic_pivot_report.md\n")
    return verdict


if __name__ == "__main__":
    main()
