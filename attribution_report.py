"""
attribution_report.py — performance attribution per strategy.

Runs holding/sector contribution + Brinson decomposition for each registered
strategy, prints a summary, and writes results/attribution.json for the dashboard.
RESEARCH ONLY — reads cached data, places no orders.

Usage:  python attribution_report.py
"""

import json
from datetime import date
from pathlib import Path

import attribution as A
import schemas
from data_io import load_panel
from strategy_base import REGISTRY

RESULTS_DIR = Path(__file__).parent / "results"


def _pct(x):
    return f"{x*100:+.2f}%"


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    panel, _ = load_panel()
    payload = {"generated": date.today().isoformat(), "strategies": {}}

    print(f"\n{'='*70}\n  PERFORMANCE ATTRIBUTION  (gross, vs equal-weight universe)\n{'='*70}")
    for name, strat in REGISTRY.items():
        att = A.attribute(strat, panel)
        payload["strategies"][name] = {"label": strat.label, **att}

        hc, br = att["holdings"], att["brinson"]
        top = sorted(hc["by_symbol"].items(), key=lambda kv: kv[1], reverse=True)
        bot = sorted(hc["by_symbol"].items(), key=lambda kv: kv[1])
        t = br["total"]
        print(f"\n  {strat.label}  ({hc['periods']} rebalance periods)")
        print(f"    Gross contribution total: {_pct(hc['total'])}")
        print(f"    Top adders:  " + ", ".join(f"{s} {_pct(c)}" for s, c in top[:3]))
        print(f"    Top drags:   " + ", ".join(f"{s} {_pct(c)}" for s, c in bot[:3]))
        print(f"    Brinson vs eq-wt universe — active {_pct(t['active_return'])}: "
              f"allocation {_pct(t['allocation'])}, selection {_pct(t['selection'])}, "
              f"interaction {_pct(t['interaction'])}")

    (RESULTS_DIR / "attribution.json").write_text(
        json.dumps(schemas.validate("attribution.json", payload), indent=2))
    print(f"\n{'='*70}\n  Saved → results/attribution.json\n")


if __name__ == "__main__":
    main()
