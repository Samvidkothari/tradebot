"""
refresh_research.py — regenerate every Research Engine JSON in one go.

Runs all six research runners in sequence so the dashboard's Analyze tabs (Tear
Sheets, Factors, Portfolio Analysis, Risk Analytics, Attribution, Data Quality)
all show current numbers. Each runner reads cached data / paper ledgers and
writes its results/*.json — RESEARCH ONLY, places no orders.

Runners are invoked in-process via their main() so a failure in one is reported
but does not stop the rest.

Usage:  python refresh_research.py
"""

import contextlib
import importlib
import io
import time
import traceback

# (module name, the results/*.json it produces)
RUNNERS = [
    ("tearsheet",          "tearsheets.json"),
    ("factor_report",      "factors.json"),
    ("portfolio_analyzer", "portfolio.json"),
    ("risk_report",        "risk.json"),
    ("attribution_report", "attribution.json"),
    ("data_quality",       "data_quality.json"),
]


def main():
    print("Refreshing all Research Engine JSONs (research only, no orders)\n")
    ok, failed = [], []
    for mod_name, out in RUNNERS:
        print(f"  • {mod_name:<20} → results/{out} ...", end=" ", flush=True)
        t0 = time.time()
        try:
            mod = importlib.import_module(mod_name)
            with contextlib.redirect_stdout(io.StringIO()):   # mute runner chatter
                mod.main()                          # each runner writes its JSON
            print(f"ok ({time.time()-t0:.1f}s)")
            ok.append(mod_name)
        except Exception:                           # one failure must not block the rest
            print("FAILED")
            traceback.print_exc()
            failed.append(mod_name)

    print(f"\nDone — {len(ok)} ok"
          + (f", {len(failed)} FAILED: {', '.join(failed)}" if failed else "")
          + ".")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
