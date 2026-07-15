"""fetch_ceinsys.py — cache daily OHLCV for CEINSYS (small-cap, ex-NIFTY50).

The main fetch_data.py only downloads the NIFTY-50 members + benchmark. CEINSYS
(Ceinsys Tech Ltd, NSE: CEINSYS) is a small-cap outside that universe, so it has
its own tiny fetcher that reuses the SAME yfinance pattern and writes into the
same data/ cache (data/CEINSYS.csv) that data_io.py reads. No login required.

Usage:
  python fetch_ceinsys.py            # download if missing
  python fetch_ceinsys.py --refresh  # force re-download

Pure data I/O — no strategy logic, no orders.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

SYMBOL = "CEINSYS"
YF_TICKER = "CEINSYS.NS"       # NSE symbol on Yahoo Finance
YEARS = 5
DATA_DIR = Path(__file__).parent / "data"


def fetch(refresh: bool = False) -> pd.DataFrame | None:
    DATA_DIR.mkdir(exist_ok=True)
    filepath = DATA_DIR / f"{SYMBOL}.csv"

    if filepath.exists() and not refresh:
        df = pd.read_csv(filepath, parse_dates=["date"])
        print(f"  {SYMBOL:<12} cached     {len(df):>5} rows  "
              f"{df['date'].min().date()} -> {df['date'].max().date()}")
        return df

    to_date = date.today()
    from_date = to_date - timedelta(days=YEARS * 366)
    try:
        raw = yf.Ticker(YF_TICKER).history(
            start=from_date, end=to_date, interval="1d", auto_adjust=True)
    except Exception as e:                                    # noqa: BLE001
        print(f"  {SYMBOL:<12} ERROR: {e}")
        return None
    if raw.empty:
        print(f"  {SYMBOL:<12} ERROR: no data returned (check ticker {YF_TICKER})")
        return None

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(filepath, index=False)

    print(f"  {SYMBOL:<12} downloaded {len(df):>5} rows  "
          f"{df['date'].min().date()} -> {df['date'].max().date()}")

    # light sanity check (small-caps are volatile; >20% days are expected, not fatal)
    big = int((df["close"].pct_change().abs() > 0.20).sum())
    if big:
        print(f"    note: {big} day(s) with >20% close move (normal for a small-cap)")
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download even if cached")
    args = ap.parse_args()
    df = fetch(refresh=args.refresh)
    print("\nCEINSYS ready in data/ — now run:  python ceinsys_analysis.py"
          if df is not None else "\nFetch failed; see error above.")
