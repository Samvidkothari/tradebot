"""
factor_report.py — current factor leaderboards + a multi-factor composite.

Loads the price/volume panel, computes every factor's normalised cross-section as
of the latest day, and a weighted multi-factor composite. Prints a leaderboard and
writes results/factors.json for the dashboard.

The composite weighting here is an ILLUSTRATIVE research blend (equal weight on
momentum + low-vol + trend), NOT a pre-registered strategy — it shows how factors
combine, nothing more. RESEARCH ONLY: reads cached data, places no orders.

Usage:  python factor_report.py
"""

import json
from datetime import date
from pathlib import Path

import factors as F
import schemas
from data_layer import MarketDataManager, FeatureStore

RESULTS_DIR = Path(__file__).parent / "results"
TOP_K       = 8

# Illustrative composite — equal weight on three independent price factors.
COMPOSITE_WEIGHTS = {"momentum": 1.0, "low_volatility": 1.0, "trend": 1.0}


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    mgr = MarketDataManager()
    store = FeatureStore(mgr)              # cached, version-keyed factor scores
    as_of = mgr.as_of()

    payload = {"generated": date.today().isoformat(), "as_of": as_of,
               "unavailable": F.UNAVAILABLE_FACTORS,
               "weights": COMPOSITE_WEIGHTS, "factors": {}, "composite": []}

    print(f"\n{'='*64}\n  FACTOR LIBRARY — leaderboards as of {as_of}  (research only)\n{'='*64}")
    for name, feat in F.FACTORS.items():
        s = store.get(name).sort_values(ascending=False)
        top = [{"symbol": sym, "score": round(float(v), 3)} for sym, v in s.head(TOP_K).items()]
        payload["factors"][name] = {"description": feat.description,
                                    "direction": feat.direction, "n": int(len(s)),
                                    "top": top}
        leaders = ", ".join(f"{d['symbol']}" for d in top[:5]) or "—"
        print(f"  {name:<16} ({feat.direction:<4}) n={len(s):<3} top: {leaders}")

    comp = store.composite(COMPOSITE_WEIGHTS)
    payload["composite"] = [{"symbol": sym, "score": round(float(v), 3)}
                            for sym, v in comp.head(TOP_K).items()]
    print(f"\n  Composite (momentum + low-vol + trend, equal weight):")
    for d in payload["composite"]:
        print(f"    {d['symbol']:<12} {d['score']:.3f}")
    print(f"\n  Fundamental factors NOT computed (no data): "
          f"{', '.join(F.UNAVAILABLE_FACTORS)}")
    print(f"{'='*64}\n")

    (RESULTS_DIR / "factors.json").write_text(
        json.dumps(schemas.validate("factors.json", payload), indent=2))
    print(f"  Saved → results/factors.json\n")


if __name__ == "__main__":
    main()
