"""
fetch_data.py — Download and cache daily OHLCV data for backtest symbols.
Uses yfinance (free, no login required).

Usage:
  python fetch_data.py            # download missing, skip cached
  python fetch_data.py --refresh  # re-download everything
"""

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────
# Full NIFTY 50 universe (current constituents) for cross-sectional momentum.
# yfinance ticker = NSE symbol + ".NS".  Some names (M&M, BAJAJ-AUTO) keep
# their punctuation. Any ticker that fails to download is skipped, not fatal —
# the validation summary shows which succeeded.
#
# NOTE (survivorship bias): this is TODAY's index membership applied to the
# past. Stocks dropped from the index over the window are absent, and current
# members were partly selected for having done well. Momentum results on this
# list will look better than a true point-in-time universe would. Flagged in
# the report too.
_NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "LTIM",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]
SYMBOLS = {name: f"{name}.NS" for name in _NIFTY50}
SYMBOLS["NIFTY50"] = "^NSEI"        # NIFTY 50 index as benchmark

YEARS    = 5
DATA_DIR = Path(__file__).parent / "data"
# ─────────────────────────────────────────────────────────────────────────────


def fetch_symbol(name, yf_ticker, from_date, to_date, refresh):
    filepath = DATA_DIR / f"{name}.csv"

    if filepath.exists() and not refresh:
        df = pd.read_csv(filepath, parse_dates=["date"])
        print(f"  {name:<20} cached     {len(df):>5} rows  "
              f"{df['date'].min().date()} → {df['date'].max().date()}")
        return df

    try:
        # .history() returns simple columns (no MultiIndex), auto-adjusted closes
        raw = yf.Ticker(yf_ticker).history(
            start=from_date, end=to_date, interval="1d", auto_adjust=True
        )
    except Exception as e:
        print(f"  {name:<20} ERROR: {e}")
        return None

    if raw.empty:
        print(f"  {name:<20} ERROR: no data returned")
        return None

    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(filepath, index=False)

    print(f"  {name:<20} downloaded {len(df):>5} rows  "
          f"{df['date'].min().date()} → {df['date'].max().date()}")
    return df


def validate(name, df):
    issues = []

    zero_vol = int((df["volume"] == 0).sum())
    if zero_vol > 5:
        issues.append(f"{zero_vol} zero-volume days")

    big_moves = int((df["close"].pct_change().abs() > 0.20).sum())
    if big_moves > 0:
        issues.append(f"{big_moves} day(s) with >20% close move")

    span_years = (df["date"].max() - df["date"].min()).days / 365
    expected   = int(span_years * 200)
    if len(df) < expected:
        issues.append(f"only {len(df)} rows, expected ≥{expected}")

    tag = "OK  " if not issues else "WARN"
    print(f"    [{tag}] {name:<20} {'; '.join(issues) if issues else ''}")


def main(refresh=False):
    DATA_DIR.mkdir(exist_ok=True)

    to_date   = date.today()
    from_date = to_date - timedelta(days=YEARS * 366)

    print(f"\nDownloading {YEARS}y daily OHLCV via yfinance  "
          f"({from_date} → {to_date}):\n")

    results = {}
    for name, ticker in SYMBOLS.items():
        df = fetch_symbol(name, ticker, from_date, to_date, refresh)
        if df is not None:
            results[name] = df

    print(f"\nValidation:\n")
    for name, df in results.items():
        validate(name, df)

    ok = len(results)
    print(f"\n{ok}/{len(SYMBOLS)} symbols ready in data/")
    if ok < len(SYMBOLS):
        missing = [n for n in SYMBOLS if n not in results]
        print(f"Missing: {', '.join(missing)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="Re-download all data even if cached")
    args = ap.parse_args()
    main(refresh=args.refresh)
