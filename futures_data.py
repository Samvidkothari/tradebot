"""
futures_data.py — Phase 0 futures data spike (continuous back-adjusted series).

The FUTURES_TRENDS_SCOPE.md Phase-0 gate: can we build ONE *correct* continuous,
back-adjusted futures price series, with roll logic and tests, from data we can
actually get? This module answers the correctness half (the engine) and provides
the acquisition half (a yfinance probe) so the data-availability half can be run
where there is a network.

Two paths, deliberately separated by data quality:

  1. build_continuous(contracts, ...)   ← the CORRECT engine.
     Given INDIVIDUAL contract series (ESH24, ESM24, …) with expiries, it rolls
     ROLL_BUFFER sessions before expiry and back-adjusts by the PROPORTIONAL
     (ratio) method: older segments are scaled so each roll seam is continuous.
     This preserves within-contract returns exactly, never goes negative, and the
     newest segment equals the real front-contract prices. Pure logic, fully
     unit-tested offline — no network, no I/O.

  2. load_yahoo_continuous(symbols) / probe_availability(...)   ← the PRAGMATIC
     free path. yfinance serves Yahoo's OWN pre-stitched continuous futures
     (e.g. "ES=F", "CL=F", "GC=F") — NOT individual contracts — so it cannot feed
     path 1 and its roll handling is Yahoo's (opaque, not back-adjusted). Usable
     for a first prototype; documented as lower-quality. Requires a network;
     yfinance is imported lazily so this module works without it.

HONEST FINDING (see results/futures_phase0.md): the engine is correct and ready,
but the individual-contract data it needs is NOT free — the free (yfinance) path
only offers pre-stitched continuous series. A true back-adjusted multi-market
sleeve needs a paid feed; the global-yfinance-continuous basket is the cheap
prototype. READ-ONLY / research. No orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ── Locked defaults (a spike, not a strategy — but keep them explicit) ────────
ROLL_BUFFER = 5          # roll this many sessions before a contract's expiry
PRICE_COLS = ("open", "high", "low", "close")

# A small, liquid, cross-asset-class default basket for the yfinance probe.
# (Yahoo continuous-future tickers — equity index, rates, metals, energy, FX, ags.)
DEFAULT_GLOBAL_BASKET = (
    "ES=F", "NQ=F", "YM=F",          # US equity index
    "ZN=F", "ZB=F",                  # US treasuries (10y, 30y)
    "GC=F", "SI=F", "HG=F",          # metals
    "CL=F", "NG=F",                  # energy
    "6E=F", "6J=F",                  # FX (EUR, JPY)
    "ZC=F", "ZW=F",                  # ags (corn, wheat)
)


@dataclass
class Contract:
    """One individual futures contract: a symbol, its expiry, and a date-indexed
    OHLC(V) DataFrame (must contain at least 'close')."""
    symbol: str
    expiry: pd.Timestamp
    df: pd.DataFrame


# ── The correct engine: roll + proportional back-adjustment ───────────────────

def _roll_date(front: Contract, back: Contract, roll_buffer: int) -> pd.Timestamp:
    """The session on which we roll from `front` to `back`: the latest date that
    (a) is ≤ front.expiry shifted back `roll_buffer` sessions, and (b) exists in
    BOTH contracts (overlap is required so the ratio is well defined)."""
    common = front.df.index.intersection(back.df.index)
    if len(common) == 0:
        raise ValueError(f"no overlapping sessions between {front.symbol} and {back.symbol}")
    # target = roll_buffer sessions before expiry, measured on the front's calendar
    fidx = front.df.index[front.df.index <= front.expiry]
    if len(fidx) == 0:
        raise ValueError(f"{front.symbol} has no sessions on/before its expiry")
    target = fidx[-(roll_buffer + 1)] if len(fidx) > roll_buffer else fidx[0]
    eligible = common[common <= target]
    return eligible[-1] if len(eligible) else common[0]


def build_continuous(contracts: list[Contract], roll_buffer: int = ROLL_BUFFER,
                     method: str = "ratio") -> pd.DataFrame:
    """Build a continuous, back-adjusted series from individual contracts.

    Returns a DataFrame indexed by date with the adjusted price columns present in
    the inputs, plus 'contract' (active symbol) and 'roll' (True on a roll date).
    The NEWEST segment is left at real prices; older segments are scaled so every
    roll seam is continuous. `method='ratio'` (proportional) preserves returns and
    is the default; `method='diff'` (Panama) preserves absolute point changes.
    Raises on <2 contracts or missing overlap (a data problem, surfaced not hidden).
    """
    if method not in ("ratio", "diff"):
        raise ValueError("method must be 'ratio' or 'diff'")
    if len(contracts) < 2:
        raise ValueError("need >= 2 contracts to build a continuous series")

    cs = sorted(contracts, key=lambda c: c.expiry)
    cols = [c for c in PRICE_COLS if c in cs[0].df.columns]
    if "close" not in cols:
        raise ValueError("contracts must have a 'close' column")

    # 1) roll dates between consecutive contracts
    rolls = [_roll_date(cs[i], cs[i + 1], roll_buffer) for i in range(len(cs) - 1)]

    # 2) each contract's active window: (prev_roll, this_roll]; newest → open end
    segments = []                                   # (contract, seg_df) oldest→newest
    lo = None
    for i, c in enumerate(cs):
        hi = rolls[i] if i < len(rolls) else None
        idx = c.df.index
        mask = np.ones(len(idx), dtype=bool)
        if lo is not None:
            mask &= idx > lo
        if hi is not None:
            mask &= idx <= hi
        seg = c.df.loc[idx[mask], cols].copy()
        if not seg.empty:
            segments.append((c, seg))
        lo = hi

    # 3) cumulative back-adjustment factor per segment (newest = identity)
    #    ratio_i makes contract i's price at roll_i equal contract i+1's there.
    ratios = []
    for i in range(len(cs) - 1):
        r = rolls[i]
        a, b = cs[i].df["close"], cs[i + 1].df["close"]
        if r not in a.index or r not in b.index:
            raise ValueError(f"roll date {r.date()} missing in a contract's index")
        if method == "ratio":
            ratios.append(float(b.loc[r] / a.loc[r]))
        else:                                       # diff/Panama: additive offset
            ratios.append(float(b.loc[r] - a.loc[r]))

    # map each *active* contract to its cumulative adjustment
    #   ratio: product of ratios from this contract forward; diff: sum forward
    adj = {}
    for i, c in enumerate(cs):
        if method == "ratio":
            f = 1.0
            for j in range(i, len(ratios)):
                f *= ratios[j]
            adj[c.symbol] = ("ratio", f)
        else:
            s = 0.0
            for j in range(i, len(ratios)):
                s += ratios[j]
            adj[c.symbol] = ("diff", s)

    # 4) stitch adjusted segments
    frames = []
    roll_set = set(rolls)
    for c, seg in segments:
        kind, k = adj[c.symbol]
        out = seg.copy()
        for col in cols:
            out[col] = out[col] * k if kind == "ratio" else out[col] + k
        out["contract"] = c.symbol
        out["roll"] = [d in roll_set for d in out.index]
        frames.append(out)

    cont = pd.concat(frames).sort_index()
    cont = cont[~cont.index.duplicated(keep="last")]
    return cont


# ── Quality report ────────────────────────────────────────────────────────────

def quality_report(cont: pd.DataFrame, name: str = "series") -> dict:
    """Basic health of a continuous series: span, rows, NaNs, calendar gaps, and
    the largest single-day return (a spike flag for a bad roll/stitch)."""
    close = cont["close"].dropna()
    if len(close) < 3:
        return {"name": name, "ok": False, "reason": "too few rows"}
    rets = close.pct_change().dropna()
    gaps = close.index.to_series().diff().dt.days.dropna()
    return {
        "name": name, "ok": True,
        "rows": int(len(close)),
        "start": str(close.index[0].date()), "end": str(close.index[-1].date()),
        "nan_pct": round(float(cont["close"].isna().mean()) * 100, 3),
        "max_gap_days": int(gaps.max()) if len(gaps) else 0,
        "max_abs_daily_ret": round(float(rets.abs().max()), 4),
        "n_rolls": int(cont.get("roll", pd.Series(dtype=bool)).sum()),
    }


# ── Pragmatic free path: yfinance continuous (runs where there's a network) ───

def load_yahoo_continuous(symbols=DEFAULT_GLOBAL_BASKET, period: str = "5y") -> dict:
    """Download Yahoo's OWN pre-stitched continuous futures via yfinance. NOTE:
    these are NOT individual contracts and are NOT back-adjusted by us — Yahoo's
    roll handling is opaque. Use for a first prototype only. Returns {symbol:
    DataFrame(open/high/low/close/volume)}. Imported lazily; needs a network."""
    import yfinance as yf                                   # lazy: keep module importable
    out = {}
    for sym in symbols:
        try:
            df = yf.download(sym, period=period, interval="1d",
                             auto_adjust=False, progress=False)
            if df is None or df.empty:
                continue
            df = df.rename(columns=str.lower)
            df.index = pd.to_datetime(df.index)
            keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
            out[sym] = df[keep].dropna(how="all")
        except Exception:
            continue
    return out


def probe_availability(symbols=DEFAULT_GLOBAL_BASKET, period: str = "5y") -> pd.DataFrame:
    """Fetch the basket and report what's actually usable — the Phase-0 data
    verdict. Returns a DataFrame (symbol, rows, span, nan%, max gap, max |ret|)."""
    data = load_yahoo_continuous(symbols, period=period)
    rows = []
    for sym in symbols:
        df = data.get(sym)
        if df is None or df.empty or "close" not in df.columns:
            rows.append({"symbol": sym, "available": False, "rows": 0})
            continue
        q = quality_report(df.assign(roll=False), name=sym)
        rows.append({"symbol": sym, "available": True, "rows": q["rows"],
                     "start": q["start"], "end": q["end"], "nan_pct": q["nan_pct"],
                     "max_gap_days": q["max_gap_days"],
                     "max_abs_daily_ret": q["max_abs_daily_ret"]})
    return pd.DataFrame(rows)


# ── CLI (manual, run where there's a network) ─────────────────────────────────

def main():  # pragma: no cover
    import sys
    if "--probe" in sys.argv:
        print("Phase-0 data probe — Yahoo continuous futures basket "
              "(pre-stitched, NOT back-adjusted):\n")
        rep = probe_availability()
        with pd.option_context("display.width", 120, "display.max_columns", 12):
            print(rep.to_string(index=False))
        avail = int(rep["available"].sum())
        print(f"\n  {avail}/{len(rep)} markets returned data. "
              f"These are Yahoo-continuous (prototype quality); a true back-adjusted "
              f"sleeve needs individual contracts from a paid feed — see "
              f"results/futures_phase0.md.")
    else:
        print("usage: python futures_data.py --probe   "
              "(build_continuous() is the tested engine; import it)")


if __name__ == "__main__":  # pragma: no cover
    main()
