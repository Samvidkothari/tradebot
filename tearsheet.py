"""
tearsheet.py — Institutional tear sheets for every research strategy.

Generic runner that feeds each strategy's daily equity curve through metrics.py
and produces a full analytics set (Sharpe/Sortino/Calmar, alpha/beta/IR vs NIFTY,
walk-forward stability, Monte Carlo robustness). Generalises the earlier
low-vol-only runner so the two equity strategies share ONE code path (no
duplication) and can be compared head-to-head.

Coverage:
  • Equity strategies (low-vol, momentum) — multi-year curves, full tear sheet.
  • Options paper books (strangle, condor) — currently too short to compute
    honest risk ratios; reported as "insufficient sample" rather than fabricating
    a Sharpe on a handful of days. They graduate to a full tear sheet once they
    have a real track record.

RESEARCH ONLY — reads cached data / paper ledgers, places no orders, re-tunes
nothing. Writes:
  • results/<name>_tearsheet.md   (one per equity strategy)
  • results/tearsheets.json       (machine-readable, for the dashboard)

Usage:  python tearsheet.py
"""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

import metrics as M
from backtest_lowvol import load_panel, run_lowvol, SPLIT_DATE
from backtest_momentum import run_momentum

BASE        = Path(__file__).parent
RESULTS_DIR = BASE / "results"
CAPITAL     = 1_000_000
MIN_DAYS    = 60          # below this, risk ratios are noise — don't report them

# Registry of equity strategies (same data panel, same benchmark, same OOS split).
EQUITY_STRATEGIES = {
    "lowvol":   ("Low-Volatility (15 lowest-vol)", run_lowvol),
    "momentum": ("Momentum (12-1, top-15)",        run_momentum),
}


# ── helpers ───────────────────────────────────────────────────────────────────

def nifty_equity(nifty_df, start, end):
    s = nifty_df.set_index("date")["close"].sort_index()
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    return (s / s.iloc[0]) if len(s) else s


def _f(x, pct=False, nd=2):
    if x is None:
        return "—"
    return f"{x*100:+.{nd}f}%" if pct else f"{x:.{nd}f}"


# ── equity-strategy tear sheet ────────────────────────────────────────────────

def equity_tearsheet(name, label, run_fn, panel_raw, nifty_df):
    equity, n_changes, _ = run_fn(panel_raw)
    if equity.empty:
        return {"name": name, "label": label, "sufficient": False,
                "note": "no equity curve produced"}
    bench_full = nifty_equity(nifty_df, equity.index[0], equity.index[-1])
    oos_eq     = equity[equity.index >= pd.Timestamp(SPLIT_DATE)]
    bench_oos  = nifty_equity(nifty_df, SPLIT_DATE, equity.index[-1])

    return {
        "name": name, "label": label, "kind": "equity", "sufficient": True,
        "n_changes": n_changes,
        "full": M.tear_sheet(equity, bench_full),
        "oos":  M.tear_sheet(oos_eq, bench_oos) if len(oos_eq) > MIN_DAYS else None,
        "walk_forward": M.walk_forward(equity, n_segments=4),
        "monte_carlo": M.monte_carlo(M.daily_returns(equity)),
    }


# ── options-book status (track record too short for honest risk ratios) ───────

def options_status(db_name, label):
    p = BASE / db_name
    if not p.exists():
        return None
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        cash = c.execute("SELECT cash FROM account WHERE id=1").fetchone()
        realised = (cash["cash"] - CAPITAL) if cash else 0.0
        n_marks = c.execute("SELECT COUNT(*) n FROM marks").fetchone()["n"]
        n_closed = c.execute("SELECT COUNT(*) n FROM cycles WHERE status='closed'").fetchone()["n"]
        last = c.execute("SELECT open_pnl FROM marks ORDER BY mark_date DESC LIMIT 1").fetchone()
        unreal = last["open_pnl"] if last else 0.0
    finally:
        c.close()
    return {"label": label, "kind": "options", "sufficient": False,
            "n_marks": n_marks, "n_closed": n_closed,
            "realised": realised, "unrealised": unreal,
            "note": f"only {n_marks} marks / {n_closed} closed cycles — risk ratios "
                    f"need a longer track record (and a vol event). Verdict still "
                    f"INCONCLUSIVE."}


# ── reporting ─────────────────────────────────────────────────────────────────

METRIC_ROWS = [
    ("CAGR", "cagr", True), ("Total return", "total_return", True),
    ("Max drawdown", "max_drawdown", True), ("Annualised vol", "annual_vol", True),
    ("Sharpe", "sharpe", False), ("Sortino", "sortino", False),
    ("Calmar", "calmar", False), ("Recovery factor", "recovery_factor", False),
    ("Profit factor (daily)", "profit_factor", False), ("Win rate (days)", "win_rate", True),
    ("Beta vs NIFTY", "beta", False), ("Alpha vs NIFTY (ann.)", "alpha", True),
    ("Information ratio", "information_ratio", False),
]


def save_md(ts):
    full, oos = ts["full"], ts["oos"]
    L = [f"# {ts['label']} — Institutional Tear Sheet\n\n",
         f"Generated: {date.today()}. Research only (simulated, no orders). "
         "Computed by `metrics.py` from the existing engine's equity curve.\n\n",
         f"Window: {full['start']} → {full['end']} ({full['n_days']} trading days). "
         f"Position-changes: {ts['n_changes']}.\n\n",
         "## Risk / return\n\n| Metric | Full | OOS (2024+) |\n|---|---|---|\n"]
    for nm, key, pct in METRIC_ROWS:
        a = _f(full[key], pct=pct)
        b = _f(oos[key], pct=pct) if oos else "—"
        L.append(f"| {nm} | {a} | {b} |\n")

    L.append("\n## Walk-forward stability\n\nParameter-free rule → rolling "
             "out-of-sample consistency check, not a parameter re-fit.\n\n")
    L.append("| Segment | CAGR | Sharpe | Max DD |\n|---|---|---|---|\n")
    for s in ts["walk_forward"]:
        L.append(f"| {s['start']} → {s['end']} | {_f(s['cagr'],pct=True)} | "
                 f"{_f(s['sharpe'])} | {_f(s['max_drawdown'],pct=True)} |\n")

    mc = ts["monte_carlo"]
    if mc:
        L.append(f"\n## Monte Carlo robustness ({mc['n_sims']} bootstraps)\n\n")
        L.append("| Outcome | p5 | p50 | p95 |\n|---|---|---|---|\n")
        L.append(f"| CAGR | {_f(mc['cagr_p5'],pct=True)} | {_f(mc['cagr_p50'],pct=True)} "
                 f"| {_f(mc['cagr_p95'],pct=True)} |\n")
        L.append(f"| Max drawdown | {_f(mc['maxdd_p5'],pct=True)} | "
                 f"{_f(mc['maxdd_p50'],pct=True)} | {_f(mc['maxdd_p95'],pct=True)} |\n")
        L.append(f"\nProbability of a negative-CAGR path: "
                 f"**{_f(mc['prob_negative_cagr'],pct=True)}**.\n")
    path = RESULTS_DIR / f"{ts['name']}_tearsheet.md"
    path.write_text("".join(L))
    return path


def print_compare(equity_sheets):
    W = 72
    print(f"\n{'='*W}")
    print("  STRATEGY TEAR-SHEET COMPARISON  (full period, research only)")
    print(f"{'='*W}")
    labels = [ts["label"].split(" (")[0] for ts in equity_sheets]
    print(f"  {'Metric':<22}" + "".join(f"{l[:14]:>16}" for l in labels))
    print(f"  {'-'*(22+16*len(labels))}")
    for nm, key, pct in METRIC_ROWS:
        cells = "".join(f"{_f(ts['full'][key], pct=pct):>16}" for ts in equity_sheets)
        print(f"  {nm:<22}{cells}")
    print(f"{'='*W}\n")


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    print("Loading data panel (shared)...", end=" ", flush=True)
    panel_raw, nifty_df = load_panel()
    print("done")

    sheets, payload = [], {"generated": date.today().isoformat(), "strategies": {}}
    for name, (label, run_fn) in EQUITY_STRATEGIES.items():
        print(f"  tear sheet: {label} ...", end=" ", flush=True)
        ts = equity_tearsheet(name, label, run_fn, panel_raw, nifty_df)
        sheets.append(ts)
        payload["strategies"][name] = ts
        if ts["sufficient"]:
            save_md(ts)
        print("done")

    for db, lbl, key in [("options.db", "Options strangle", "strangle"),
                         ("condor.db", "Options condor", "condor")]:
        st = options_status(db, lbl)
        if st:
            payload["strategies"][key] = st

    (RESULTS_DIR / "tearsheets.json").write_text(json.dumps(payload, indent=2, default=str))
    print_compare(sheets)
    print(f"  Saved per-strategy reports + results/tearsheets.json\n")


if __name__ == "__main__":
    main()
