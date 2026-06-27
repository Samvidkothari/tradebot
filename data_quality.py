"""
data_quality.py — Market-data validation (Research Engine).

Systematic quality checks over the cached price/volume panel (data/*.csv), so
data problems surface explicitly instead of silently poisoning a backtest. The
existing fetch_data.py had ad-hoc validation; this centralises it into reusable,
tested functions + a per-symbol report.

Checks per symbol:
  • coverage / missing days — vs the panel's own trading calendar (the union of
    all symbols' dates, so holidays don't cause false positives);
  • staleness — does this symbol's history end before the panel does (delisted /
    broken feed, e.g. LTIM, TATAMOTORS);
  • zero-volume days, extreme daily moves (>20%, possible split/error or real
    event), non-positive prices, duplicate or unsorted dates;
  • whole-cache staleness vs a reference date.

Each symbol gets OK / WARN / FAIL. RESEARCH ONLY — reads cached files, writes a
report, places no orders and changes no data.

Usage:  python data_quality.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import data_io
import schemas

DATA_DIR    = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

# Thresholds (config, not magic numbers buried in logic).
EXTREME_MOVE   = 0.20    # |daily return| above this is flagged
ZERO_VOL_WARN  = 5       # more than this many zero-volume days → WARN
MISSING_WARN   = 5       # more than this many missing active days → WARN
COVERAGE_WARN  = 0.95    # coverage below this → WARN
STALE_FAIL     = 10      # ending >this many trading days early → FAIL (likely dead)
ACTIVE_FRAC    = 0.5     # a date is "market-active" if >= this fraction have data


# ── pure check helpers ────────────────────────────────────────────────────────

def count_extreme_moves(close: pd.Series, threshold: float = EXTREME_MOVE) -> int:
    return int((close.pct_change().abs() > threshold).sum())


def count_zero_volume(volume: pd.Series) -> int:
    return int((volume == 0).sum()) if volume is not None else 0


def count_nonpositive(close: pd.Series) -> int:
    return int((close <= 0).sum())


def has_duplicate_dates(dates: pd.Series) -> bool:
    return bool(pd.Index(dates).duplicated().any())


def is_sorted(dates: pd.Series) -> bool:
    return bool(pd.Index(dates).is_monotonic_increasing)


def staleness_days(last_date, ref_date) -> int:
    return int(np.busday_count(pd.Timestamp(last_date).date().isoformat(),
                               pd.Timestamp(ref_date).date().isoformat()))


# ── panel validation ──────────────────────────────────────────────────────────

def validate_panel(data_dir: Path = DATA_DIR, ref_date=None) -> dict:
    ref_date = pd.Timestamp(ref_date or date.today())
    raws = data_io.symbol_frames(data_dir, exclude_index=True)

    if not raws:
        return {"error": "no data CSVs found"}

    close = pd.DataFrame({s: df.set_index("date")["close"] for s, df in raws.items()}).sort_index()
    panel_end = close.index.max()
    active = close.notna().sum(axis=1) >= ACTIVE_FRAC * close.shape[1]

    syms, n_ok = [], {"OK": 0, "WARN": 0, "FAIL": 0}
    for s, df in raws.items():
        df = df.sort_values("date")
        c = df.set_index("date")["close"]
        v = df.set_index("date").get("volume")
        last = df["date"].max()
        present = close[s].notna()
        missing_active = int((active & ~present).sum())
        coverage = float(present.sum() / present.size)
        stale = staleness_days(last, panel_end)

        issues = []
        nonpos = count_nonpositive(c)
        dup = has_duplicate_dates(df["date"])
        zv = count_zero_volume(v)
        ext = count_extreme_moves(c)

        status = "OK"
        if nonpos > 0:
            issues.append(f"{nonpos} non-positive prices"); status = "FAIL"
        if dup:
            issues.append("duplicate dates"); status = "FAIL"
        if not is_sorted(df["date"]):
            issues.append("unsorted dates"); status = "FAIL"
        if stale > STALE_FAIL:
            issues.append(f"history ends {stale} trading days early"); status = "FAIL"
        if status != "FAIL":
            if coverage < COVERAGE_WARN:
                issues.append(f"coverage {coverage*100:.0f}%"); status = "WARN"
            if missing_active > MISSING_WARN:
                issues.append(f"{missing_active} missing active days"); status = "WARN"
            if zv > ZERO_VOL_WARN:
                issues.append(f"{zv} zero-volume days"); status = "WARN"
            if ext > 0:
                issues.append(f"{ext} extreme moves"); status = "WARN" if status == "OK" else status

        n_ok[status] += 1
        syms.append({"symbol": s, "rows": int(len(df)), "last_date": str(last.date()),
                     "coverage": round(coverage, 3), "missing_active": missing_active,
                     "zero_vol_days": zv, "extreme_moves": ext, "nonpositive": nonpos,
                     "stale_days": stale, "status": status, "issues": issues})

    syms.sort(key=lambda d: ({"FAIL": 0, "WARN": 1, "OK": 2}[d["status"]], d["symbol"]))
    cache_stale = staleness_days(panel_end, ref_date)
    return {
        "generated": date.today().isoformat(),
        "panel_start": str(close.index.min().date()),
        "panel_end": str(panel_end.date()),
        "n_symbols": len(syms),
        "cache_stale_days": cache_stale,
        "summary": n_ok,
        "symbols": syms,
    }


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    r = validate_panel()
    if "error" in r:
        print(r["error"]); return
    W = 70
    print(f"\n{'='*W}\n  DATA-QUALITY REPORT  (research only)\n{'='*W}")
    print(f"  Panel: {r['panel_start']} → {r['panel_end']}   {r['n_symbols']} symbols   "
          f"cache {r['cache_stale_days']} trading days behind today")
    s = r["summary"]
    print(f"  Status: {s['OK']} OK · {s['WARN']} WARN · {s['FAIL']} FAIL\n")
    flagged = [x for x in r["symbols"] if x["status"] != "OK"]
    if flagged:
        print(f"  {'Symbol':<14}{'Status':<7}Issues")
        for x in flagged:
            print(f"  {x['symbol']:<14}{x['status']:<7}{'; '.join(x['issues'])}")
    else:
        print("  All symbols clean.")
    print(f"{'='*W}\n")
    (RESULTS_DIR / "data_quality.json").write_text(
        json.dumps(schemas.validate("data_quality.json", r), indent=2))
    print(f"  Saved → results/data_quality.json\n")


if __name__ == "__main__":
    main()
