"""
market_intel.py — Indian market intelligence layer.

Pulls together the India-specific market knowledge the platform needs:

  • NSE holidays + sessions          → TradingCalendar (data-derived, accurate)
  • Weekly / monthly F&O expiries    → ExpiryCalendar (Thursday convention, holiday-adjusted)
  • Sector classification            → UniverseManager (config-driven)
  • Corporate actions (split/div)    → fetch_actions() pulls REAL splits + dividends
                                       from yfinance; surfaced from data/_actions.json
  • Surveillance (ASM/GSM) + circuits→ surveillance.json (config slots) + a filter +
                                       circuit-like-move detection

HONEST data scope: NSE holidays/expiries/sectors and split/dividend history are
real. **ASM/GSM lists, per-stock circuit limits, bonus-vs-split disambiguation, and
rights issues need an official NSE feed we don't have** — those are config slots /
approximations, clearly flagged, never fabricated. READ-ONLY; no orders.

Usage:  python market_intel.py            # write results/market_intel.json
        python market_intel.py --fetch    # also refresh real corporate actions (network)
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

import data_io
from expiry_calendar import ExpiryCalendar
from trading_calendar import TradingCalendar
from universe import UniverseManager

SURVEILLANCE_PATH = Path(__file__).parent / "surveillance.json"
ACTIONS_PATH      = data_io.DATA_DIR / "_actions.json"
RESULTS_DIR       = Path(__file__).parent / "results"


def _surveillance() -> dict:
    cfg = json.loads(SURVEILLANCE_PATH.read_text())
    return {"asm": list(cfg.get("asm", [])), "gsm": list(cfg.get("gsm", [])),
            "circuit_band": float(cfg.get("circuit_band", 0.20))}


def flagged_symbols() -> set[str]:
    s = _surveillance()
    return set(s["asm"]) | set(s["gsm"])


def filter_universe(symbols) -> list[str]:
    """Drop ASM/GSM-flagged names from a symbol list (surveillance filter)."""
    bad = flagged_symbols()
    return [s for s in symbols if s not in bad]


def circuit_events(panel: pd.DataFrame, band: float, lookback: int = 252) -> list[dict]:
    """Approximate circuit hits: days where |daily move| >= band. (NSE sets per-stock
    bands; without that feed this is a single-band approximation, not the truth.)"""
    df = panel.tail(lookback + 1)
    out = []
    for sym in df.columns:
        chg = df[sym].pct_change()
        for d, v in chg[chg.abs() >= band].items():
            out.append({"symbol": sym, "date": d.date().isoformat(), "move": round(float(v), 3)})
    return out


def fetch_actions(symbols=None) -> int:
    """NETWORK: pull REAL splits + dividends from yfinance → data/_actions.json.
    (Bonus issues usually appear as splits in yfinance; rights issues are not
    available.) Not run in the daily refresh."""
    import yfinance as yf
    syms = symbols or [s for s in data_io.close_panel().columns]
    store = {}
    for s in syms:
        try:
            acts = yf.Ticker(f"{s}.NS").actions
        except Exception:
            continue
        if acts is None or acts.empty:
            continue
        rows = []
        for d, r in acts.iterrows():
            div, split = float(r.get("Dividends", 0) or 0), float(r.get("Stock Splits", 0) or 0)
            if div:
                rows.append({"date": str(pd.Timestamp(d).date()), "type": "dividend", "value": div})
            if split:
                rows.append({"date": str(pd.Timestamp(d).date()), "type": "split", "value": split})
        if rows:
            store[s] = rows
    ACTIONS_PATH.write_text(json.dumps(store, indent=2))
    return sum(len(v) for v in store.values())


def _recent_actions(n: int = 8) -> dict:
    if not ACTIONS_PATH.exists():
        return {"available": False, "note": "run `python market_intel.py --fetch` "
                "for real splits/dividends (yfinance); bonus≈split, rights unavailable",
                "recent": []}
    store = json.loads(ACTIONS_PATH.read_text())
    flat = [{"symbol": s, **a} for s, rows in store.items() for a in rows]
    flat.sort(key=lambda x: x["date"], reverse=True)
    return {"available": True, "n": len(flat), "recent": flat[:n]}


def summary() -> dict:
    cal = TradingCalendar()
    exp = ExpiryCalendar(cal)
    um = UniverseManager()
    today = pd.Timestamp.today().normalize()
    panel = data_io.close_panel()

    nxt_w, nxt_m = exp.next_weekly(today), exp.next_monthly(today)
    syms = um.resolve("NIFTY50")
    sectors = {}
    for s in syms:
        sec = um.sector_of(s)
        sectors[sec] = sectors.get(sec, 0) + 1

    surv = _surveillance()
    circuits = circuit_events(panel, surv["circuit_band"])
    return {
        "generated": date.today().isoformat(),
        "as_of": panel.index[-1].date().isoformat() if len(panel) else None,
        "sessions": len(cal.sessions()),
        "expiries": {
            "next_weekly": nxt_w.date().isoformat(), "days_to_weekly": exp.days_to(nxt_w, today),
            "next_monthly": nxt_m.date().isoformat(), "days_to_monthly": exp.days_to(nxt_m, today),
        },
        "sectors": dict(sorted(sectors.items(), key=lambda kv: kv[1], reverse=True)),
        "n_symbols": len(syms),
        "surveillance": {"asm": surv["asm"], "gsm": surv["gsm"],
                         "flagged": sorted(flagged_symbols()),
                         "note": "ASM/GSM need an official NSE feed — config slots"},
        "circuits": {"band": surv["circuit_band"], "events": len(circuits),
                     "recent": sorted(circuits, key=lambda x: x["date"], reverse=True)[:8],
                     "note": "approx (|move| >= band); per-stock bands need a feed"},
        "corporate_actions": _recent_actions(),
    }


def main(do_fetch: bool = False):
    import schemas
    RESULTS_DIR.mkdir(exist_ok=True)
    if do_fetch:
        n = fetch_actions()
        print(f"  Fetched {n} corporate-action records → {ACTIONS_PATH.name}")
    s = summary()
    (RESULTS_DIR / "market_intel.json").write_text(
        json.dumps(schemas.validate("market_intel.json", s), indent=2))
    e = s["expiries"]
    print(f"  Market intel: next weekly expiry {e['next_weekly']} ({e['days_to_weekly']}d), "
          f"monthly {e['next_monthly']} ({e['days_to_monthly']}d); "
          f"{s['n_symbols']} symbols / {len(s['sectors'])} sectors; "
          f"{s['circuits']['events']} circuit-like moves → results/market_intel.json")


if __name__ == "__main__":
    import sys
    main(do_fetch="--fetch" in sys.argv)
