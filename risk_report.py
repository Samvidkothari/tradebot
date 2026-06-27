"""
risk_report.py — risk analytics for each strategy + ATR sizing illustration.

Builds each strategy's equity curve (shared engine) and runs it through
risk_analytics.py: VaR/CVaR, drawdown analytics, tail stats, vol-target scale.
Also prints an ATR-based position-sizing illustration for the current low-vol
holdings. Writes results/risk.json for the dashboard.

RESEARCH ONLY — reads cached data / paper ledgers, places no orders, and applies
nothing to the live book.

Usage:  python risk_report.py
"""

from config import PAPER_CAPITAL
import json
from datetime import date
from pathlib import Path

import pandas as pd

import risk_analytics as RA
from data_io import load_panel
from strategy_base import REGISTRY, MonthlyRebalanceEngine
from portfolio_analyzer import load_holdings, load_closes

RESULTS_DIR = Path(__file__).parent / "results"
CAPITAL = PAPER_CAPITAL
ENGINE = MonthlyRebalanceEngine()


def _pct(x):
    return "—" if x is None else f"{x*100:+.2f}%"


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    panel_raw, _ = load_panel()

    payload = {"generated": date.today().isoformat(), "strategies": {}, "atr_sizing": []}
    print(f"\n{'='*72}\n  RISK ANALYTICS — per strategy  (research only, daily figures)\n{'='*72}")
    print(f"  {'Strategy':<14}{'VaR95':>9}{'CVaR95':>9}{'VaR99':>9}"
          f"{'AnnVol':>9}{'MaxDD':>9}{'CurDD':>9}{'Ulcer':>8}")
    for name, strat in REGISTRY.items():
        equity, _, _ = ENGINE.run(strat, panel_raw)
        rr = RA.risk_report(equity)
        payload["strategies"][name] = {"label": strat.label, **rr}
        v, d, t = rr["var"], rr["drawdown"], rr["tail"]
        print(f"  {name:<14}{_pct(v['hist_95']):>9}{_pct(v['cvar_95']):>9}"
              f"{_pct(v['hist_99']):>9}{_pct(t.get('ann_vol')):>9}"
              f"{_pct(d['max_drawdown']):>9}{_pct(d['current_drawdown']):>9}"
              f"{(d['ulcer_index'] or 0)*100:>7.1f}%")

    # Volatility-targeting illustration.
    print(f"\n  Volatility targeting (to 10% annual — illustration, NOT applied):")
    for name, s in payload["strategies"].items():
        vt = s["vol_target_10pct"]
        if vt["scale"]:
            lever = "lever up" if vt["scale"] > 1 else "de-risk"
            print(f"    {name:<14} realised {vt['realised_ann_vol']*100:.1f}%  "
                  f"→ scale {vt['scale']:.2f}x ({lever})")

    # ATR-based sizing illustration for current holdings.
    holdings = load_holdings()
    if holdings:
        syms = [h["symbol"] for h in holdings]
        closes = load_closes(syms)
        print(f"\n  ATR(14) position sizing — risk 1% of ₹{CAPITAL:,} per name, 2xATR stop "
              f"(illustration):")
        for h in holdings[:6]:
            s = h["symbol"]
            fp = Path("data") / f"{s}.csv"
            if not fp.exists():
                continue
            df = pd.read_csv(fp, parse_dates=["date"])
            a = RA.atr(df["high"], df["low"], df["close"])
            size = RA.position_size_atr(CAPITAL, 0.01, a, multiplier=2.0)
            payload["atr_sizing"].append({"symbol": s, "atr": a, **size})
            if a:
                print(f"    {s:<12} ATR {a:7.1f}  → {size['units']:>4} units "
                      f"(stop ₹{size['stop_distance']:.0f})")
    print(f"{'='*72}\n")

    (RESULTS_DIR / "risk.json").write_text(json.dumps(payload, indent=2))
    print(f"  Saved → results/risk.json\n")


if __name__ == "__main__":
    main()
