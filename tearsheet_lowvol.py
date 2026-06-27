"""
tearsheet_lowvol.py — Institutional tear sheet for the low-vol strategy.

Runs the EXISTING low-vol engine (backtest_lowvol.run_lowvol — unchanged) to get
its daily equity curve, then runs that curve through metrics.py to produce the
full analytics set the pre-registered pass/fail report does NOT show: Sharpe,
Sortino, Calmar, alpha/beta/IR vs NIFTY, walk-forward stability, and a Monte
Carlo robustness distribution.

This is RESEARCH ONLY — it reads cached data, places no orders, and does not
touch or re-tune the strategy. It deepens the validation of the one strategy
that passed (low-vol) before it is trusted further in Phase 3 paper trading.

Usage:  python tearsheet_lowvol.py
Output: terminal tear sheet + results/lowvol_tearsheet.md
"""

from datetime import date
from pathlib import Path

import pandas as pd

import metrics as M
from backtest_lowvol import load_panel, run_lowvol, SPLIT_DATE

RESULTS_DIR = Path(__file__).parent / "results"


def nifty_equity(nifty_df, start, end):
    """NIFTY 50 buy-and-hold equity curve over [start, end], normalised to 1.0."""
    s = nifty_df.set_index("date")["close"].sort_index()
    s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
    return (s / s.iloc[0]) if len(s) else s


def _f(x, pct=False, nd=2):
    if x is None:
        return "—"
    return f"{x*100:+.{nd}f}%" if pct else f"{x:.{nd}f}"


def print_tearsheet(full, oos, wf, mc):
    W = 76
    print(f"\n{'='*W}")
    print("  LOW-VOLATILITY — INSTITUTIONAL TEAR SHEET  (research only, simulated)")
    print(f"{'='*W}")
    print(f"  Window: {full['start']} → {full['end']}  ({full['n_days']} days)\n")

    rows = [
        ("CAGR",               _f(full["cagr"], pct=True),    _f(oos["cagr"], pct=True)),
        ("Total return",       _f(full["total_return"], pct=True), _f(oos["total_return"], pct=True)),
        ("Max drawdown",       _f(full["max_drawdown"], pct=True), _f(oos["max_drawdown"], pct=True)),
        ("Annualised vol",     _f(full["annual_vol"], pct=True),   _f(oos["annual_vol"], pct=True)),
        ("Sharpe",             _f(full["sharpe"]),            _f(oos["sharpe"])),
        ("Sortino",            _f(full["sortino"]),           _f(oos["sortino"])),
        ("Calmar",             _f(full["calmar"]),            _f(oos["calmar"])),
        ("Recovery factor",    _f(full["recovery_factor"]),   _f(oos["recovery_factor"])),
        ("Profit factor (d)",  _f(full["profit_factor"]),     _f(oos["profit_factor"])),
        ("Win rate (days)",    _f(full["win_rate"], pct=True),_f(oos["win_rate"], pct=True)),
        ("Beta vs NIFTY",      _f(full["beta"]),              _f(oos["beta"])),
        ("Alpha vs NIFTY",     _f(full["alpha"], pct=True),   _f(oos["alpha"], pct=True)),
        ("Information ratio",  _f(full["information_ratio"]), _f(oos["information_ratio"])),
    ]
    print(f"  {'Metric':<20}{'Full':>14}{'OOS (2024+)':>16}")
    print(f"  {'-'*50}")
    for name, a, b in rows:
        print(f"  {name:<20}{a:>14}{b:>16}")

    print(f"\n  Walk-forward stability (contiguous segments):")
    print(f"  {'Segment':<22}{'CAGR':>10}{'Sharpe':>10}{'MaxDD':>10}")
    for s in wf:
        print(f"  {s['start']}→{s['end'][:7]:<8}{_f(s['cagr'],pct=True):>10}"
              f"{_f(s['sharpe']):>10}{_f(s['max_drawdown'],pct=True):>10}")

    if mc:
        print(f"\n  Monte Carlo robustness ({mc['n_sims']} bootstraps):")
        print(f"    CAGR    p5 {_f(mc['cagr_p5'],pct=True)}   "
              f"p50 {_f(mc['cagr_p50'],pct=True)}   p95 {_f(mc['cagr_p95'],pct=True)}")
        print(f"    MaxDD   p5 {_f(mc['maxdd_p5'],pct=True)}   "
              f"p50 {_f(mc['maxdd_p50'],pct=True)}   p95 {_f(mc['maxdd_p95'],pct=True)}")
        print(f"    P(negative CAGR): {_f(mc['prob_negative_cagr'],pct=True)}")
    print(f"{'='*W}\n")


def save_report(full, oos, wf, mc):
    RESULTS_DIR.mkdir(exist_ok=True)
    L = ["# Low-Volatility — Institutional Tear Sheet\n\n",
         f"Generated: {date.today()}. Research only (simulated, no orders). "
         "Computed by `metrics.py` from the existing low-vol equity curve; the "
         "strategy itself is unchanged.\n\n",
         f"Window: {full['start']} → {full['end']} ({full['n_days']} trading days).\n\n",
         "## Risk / return\n\n",
         "| Metric | Full | OOS (2024+) |\n|---|---|---|\n"]
    rows = [
        ("CAGR", _f(full["cagr"], pct=True), _f(oos["cagr"], pct=True)),
        ("Total return", _f(full["total_return"], pct=True), _f(oos["total_return"], pct=True)),
        ("Max drawdown", _f(full["max_drawdown"], pct=True), _f(oos["max_drawdown"], pct=True)),
        ("Annualised vol", _f(full["annual_vol"], pct=True), _f(oos["annual_vol"], pct=True)),
        ("Sharpe", _f(full["sharpe"]), _f(oos["sharpe"])),
        ("Sortino", _f(full["sortino"]), _f(oos["sortino"])),
        ("Calmar", _f(full["calmar"]), _f(oos["calmar"])),
        ("Recovery factor", _f(full["recovery_factor"]), _f(oos["recovery_factor"])),
        ("Profit factor (daily)", _f(full["profit_factor"]), _f(oos["profit_factor"])),
        ("Win rate (days)", _f(full["win_rate"], pct=True), _f(oos["win_rate"], pct=True)),
        ("Beta vs NIFTY", _f(full["beta"]), _f(oos["beta"])),
        ("Alpha vs NIFTY (ann.)", _f(full["alpha"], pct=True), _f(oos["alpha"], pct=True)),
        ("Information ratio", _f(full["information_ratio"]), _f(oos["information_ratio"])),
    ]
    for n, a, b in rows:
        L.append(f"| {n} | {a} | {b} |\n")

    L.append("\n## Walk-forward stability\n\n")
    L.append("Parameter-free rule → this is a rolling out-of-sample consistency "
             "check, not a parameter re-fit.\n\n")
    L.append("| Segment | CAGR | Sharpe | Max DD |\n|---|---|---|---|\n")
    for s in wf:
        L.append(f"| {s['start']} → {s['end']} | {_f(s['cagr'],pct=True)} | "
                 f"{_f(s['sharpe'])} | {_f(s['max_drawdown'],pct=True)} |\n")

    if mc:
        L.append(f"\n## Monte Carlo robustness ({mc['n_sims']} bootstraps)\n\n")
        L.append("Resamples daily returns with replacement to test dependence on "
                 "the realised return *sequence*.\n\n")
        L.append("| Outcome | p5 | p50 | p95 |\n|---|---|---|---|\n")
        L.append(f"| CAGR | {_f(mc['cagr_p5'],pct=True)} | {_f(mc['cagr_p50'],pct=True)} "
                 f"| {_f(mc['cagr_p95'],pct=True)} |\n")
        L.append(f"| Max drawdown | {_f(mc['maxdd_p5'],pct=True)} | "
                 f"{_f(mc['maxdd_p50'],pct=True)} | {_f(mc['maxdd_p95'],pct=True)} |\n")
        L.append(f"\nProbability of a negative-CAGR path: "
                 f"**{_f(mc['prob_negative_cagr'],pct=True)}**.\n")

    L.append("\n## Notes\n\n"
             "- Survivorship bias unchanged from the main report (today's NIFTY 50 "
             "applied to the past).\n"
             "- Profit factor / win rate are **daily-bar** proxies (this is a "
             "monthly-rebalanced book, not a per-trade strategy).\n"
             "- Benchmark = NIFTY 50 buy-and-hold over the same window.\n")
    path = RESULTS_DIR / "lowvol_tearsheet.md"
    path.write_text("".join(L))
    return path


def main():
    print("Building low-vol equity curve (existing engine, unchanged)...", end=" ", flush=True)
    panel_raw, nifty_df = load_panel()
    equity, n_changes, _ = run_lowvol(panel_raw)
    print("done")

    bench_full = nifty_equity(nifty_df, equity.index[0], equity.index[-1])
    oos_eq     = equity[equity.index >= pd.Timestamp(SPLIT_DATE)]
    bench_oos  = nifty_equity(nifty_df, SPLIT_DATE, equity.index[-1])

    full = M.tear_sheet(equity, bench_full)
    oos  = M.tear_sheet(oos_eq, bench_oos)
    wf   = M.walk_forward(equity, n_segments=4)
    mc   = M.monte_carlo(M.daily_returns(equity))

    print_tearsheet(full, oos, wf, mc)
    path = save_report(full, oos, wf, mc)
    print(f"  Tear sheet saved → {path}\n")


if __name__ == "__main__":
    main()
