"""backtest_priceaction.py — after-cost backtest of the price-action swing spec.

Pools every mechanical trade from priceaction.generate_trades() across the NIFTY
universe, applies the real round-trip cost, and judges the result against the
pre-registered pass criteria in strategies/SPEC_priceaction.md. READ-ONLY /
research: reads data/*.csv, writes results/priceaction_report.md, places nothing.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config
import data_io
import priceaction as PA

RESULTS_DIR = Path(__file__).parent / "results"
COST = config.COST_ROUNDTRIP
SPLIT = pd.Timestamp(config.SPLIT_DATE)
RISK_PER_TRADE = 0.01     # 1% equity risk per trade
W = 92


def _collect() -> pd.DataFrame:
    frames = data_io.symbol_frames(exclude_index=True)
    rows = []
    for sym, df in frames.items():
        for t in PA.generate_trades(df):
            net = t["gross_ret"] - COST                 # subtract round-trip cost
            r = net / t["risk"] if t["risk"] > 0 else 0.0   # net P&L in R multiples
            rows.append({"symbol": sym, "exit_date": pd.Timestamp(t["exit_date"]),
                         "side": t["side"], "reason": t["reason"], "R": r})
    if not rows:
        return pd.DataFrame(columns=["symbol", "exit_date", "side", "reason", "R"])
    return pd.DataFrame(rows).sort_values("exit_date").reset_index(drop=True)


def _stats(R: pd.Series) -> dict:
    n = len(R)
    if n == 0:
        return {"n": 0}
    wins, losses = R[R > 0], R[R <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    eq = (1 + RISK_PER_TRADE * R).cumprod()
    peak = eq.cummax()
    maxdd = float((eq / peak - 1).min())
    return {"n": n, "expectancy": float(R.mean()), "win_rate": float((R > 0).mean()),
            "profit_factor": float(pf), "total_return": float(eq.iloc[-1] - 1),
            "max_dd": maxdd, "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0}


def _verdict(full: dict, oos: dict) -> tuple:
    checks = [
        ("Positive net expectancy (mean R > 0)", full["n"] and full["expectancy"] > 0),
        ("Profit factor >= 1.3", full["n"] and full["profit_factor"] >= 1.3),
        ("Holds out-of-sample (OOS mean R > 0)", oos.get("n", 0) and oos["expectancy"] > 0),
        (">= 100 trades", full["n"] >= 100),
    ]
    passed = all(ok for _, ok in checks)
    if full["n"] < 100:
        return "INCONCLUSIVE", checks
    return ("PASS" if passed else "FAIL"), checks


def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "no trades"
    return (f"{s['n']} trades · expectancy {s['expectancy']:+.3f}R · win {s['win_rate']*100:.0f}% · "
            f"PF {s['profit_factor']:.2f} · total {s['total_return']*100:+.1f}% · maxDD {s['max_dd']*100:.1f}%")


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    df = _collect()
    full = _stats(df["R"]) if len(df) else {"n": 0}
    oos = _stats(df[df["exit_date"] >= SPLIT]["R"]) if len(df) else {"n": 0}
    verdict, checks = _verdict(full, oos) if full.get("n") else ("INCONCLUSIVE", [])

    print(f"\n{'='*W}\n  PRICE-ACTION SWING — after-cost backtest (pre-registered)\n{'='*W}")
    print(f"  Cost/round-trip: {COST*100:.3f}%   ·   OOS split: {config.SPLIT_DATE}")
    print(f"  Full period : {_fmt(full)}")
    print(f"  Out-of-sample: {_fmt(oos)}")
    print(f"  {'-'*W}")
    for name, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  VERDICT: {verdict}\n{'='*W}\n")

    # report
    def row(s):
        return ("| — | 0 | — | — | — | — |" if not s.get("n") else
                f"| {s['n']} | {s['win_rate']*100:.0f}% | {s['expectancy']:+.3f} | "
                f"{s['profit_factor']:.2f} | {s['total_return']*100:+.1f}% | {s['max_dd']*100:.1f}% |")
    by_reason = (df.groupby("reason")["R"].agg(["count", "mean"]) if len(df) else pd.DataFrame())
    md = [
        "# Price-Action Swing — Backtest Report",
        f"\nGenerated: {datetime.now():%Y-%m-%d}  ",
        "Spec: `strategies/SPEC_priceaction.md` (pre-registered).  ",
        "Mechanical market-structure + supply/demand + R:R≥2.5, long & short, daily bars.  ",
        f"Costs: {COST*100:.3f}% per round trip (config.COST_ROUNDTRIP). 1% equity risk/trade.\n",
        f"## Verdict: **{verdict}**\n",
        "| Window | Trades | Win% | Expectancy (R) | Profit factor | Total | Max DD |",
        "|---|---|---|---|---|---|---|",
        f"| Full 2021→now {row(full)}",
        f"| Out-of-sample (≥{config.SPLIT_DATE}) {row(oos)}",
        "\n### Pass criteria (judged after costs)\n",
    ]
    for name, ok in checks:
        md.append(f"- {'✅' if ok else '❌'} {name}")
    if len(by_reason):
        md += ["\n### Exit breakdown\n", "| Exit | Trades | Mean R |", "|---|---|---|"]
        for reason, r in by_reason.iterrows():
            md.append(f"| {reason} | {int(r['count'])} | {r['mean']:+.3f} |")
    md += [
        "\n### How to read this\n",
        "Each trade's P&L is expressed in **R** (multiples of the risk taken to its "
        "stop), net of the round-trip cost. Expectancy is the average R per trade — "
        "positive means the edge survives costs. This is the honest test the "
        "discretionary video version never runs: mechanical zones, real costs, and "
        "an out-of-sample split so the rules can't be fitted to the outcome.\n",
        "Verdicts follow the pre-registered criteria; parameters were fixed before the "
        "run and are not tuned to this result.",
    ]
    (RESULTS_DIR / "priceaction_report.md").write_text("\n".join(md))
    print(f"  Report saved → results/priceaction_report.md\n")
    return verdict


if __name__ == "__main__":
    main()
