"""
market_data.py — throttled, retrying yfinance fetch layer (hourly + daily).

Why this exists: the higher-frequency runner (intraday_mark.py, driven by
scheduler.py) polls prices repeatedly during the session. Hammering Yahoo with
back-to-back requests is the classic way to get an IP temporarily blocked
(HTTP 429 / empty frames). This module is the ONE place fetch discipline lives:

  • Throttle   — a global minimum gap between calls (MIN_INTERVAL_S), so a
                 50-symbol sweep is spread out instead of bursted.
  • Retry      — exponential backoff with jitter on transient failures;
                 rate-limit-looking errors ("429", "rate", "too many") get a
                 longer cool-off before the retry.
  • Fail-soft  — every public function returns None / partial dict instead of
                 raising; a missing price must never kill a scheduled run
                 (same doctrine as run_paper_bot.sh's non-fatal fetch).

Intervals: "60m" bars reach back ~730 days on Yahoo; "1d" is unlimited. The
hourly mark job only needs the LAST close, so fetch windows are kept small
(days, not years) to stay light on the API.

READ-ONLY market data; no orders. Backtest CSVs in data/ are never touched.
"""

from __future__ import annotations

import random
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

# ── Fetch discipline knobs ────────────────────────────────────────────────────
MIN_INTERVAL_S   = 0.60   # global floor between yfinance calls (~100 req/min max)
MAX_RETRIES      = 4      # attempts per symbol before giving up (fail-soft)
BACKOFF_BASE_S   = 2.0    # sleep = BACKOFF_BASE_S * 2**attempt (+ jitter)
RATE_LIMIT_EXTRA = 30.0   # additional cool-off when the error smells like a 429
# ─────────────────────────────────────────────────────────────────────────────

_last_call_ts = 0.0       # module-global: shared by every caller in-process


def _throttle() -> None:
    """Enforce the global minimum gap between outbound calls."""
    global _last_call_ts
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call_ts)
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


def _looks_rate_limited(err: Exception) -> bool:
    msg = str(err).lower()
    return any(t in msg for t in ("429", "rate", "too many", "temporarily blocked"))


def fetch_history(ticker: str, interval: str = "60m",
                  lookback_days: int = 7) -> pd.DataFrame | None:
    """Fetch OHLCV bars with throttling + retries. Returns a DataFrame with a
    tz-naive DatetimeIndex and lowercase columns, or None after MAX_RETRIES."""
    end = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=lookback_days)

    for attempt in range(MAX_RETRIES):
        _throttle()
        try:
            raw = yf.Ticker(ticker).history(
                start=start, end=end, interval=interval, auto_adjust=True,
            )
            if raw is None or raw.empty:
                raise ValueError("empty frame returned")
            df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["open", "high", "low", "close", "volume"]
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df.sort_index()
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                print(f"  market_data: {ticker} {interval} failed after "
                      f"{MAX_RETRIES} tries ({e})")
                return None
            sleep = BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, 1)
            if _looks_rate_limited(e):
                sleep += RATE_LIMIT_EXTRA
                print(f"  market_data: rate-limit hint from Yahoo — cooling off "
                      f"{sleep:.0f}s before retrying {ticker}")
            time.sleep(sleep)
    return None


def fetch_last_price(ticker: str) -> float | None:
    """Freshest available close for `ticker`: latest 60m bar, falling back to
    the latest daily bar (e.g. very early in the session, or 60m outage)."""
    bars = fetch_history(ticker, interval="60m", lookback_days=5)
    if bars is None or bars.empty:
        bars = fetch_history(ticker, interval="1d", lookback_days=10)
    if bars is None or bars.empty:
        return None
    px = float(bars["close"].iloc[-1])
    return px if px > 0 else None


def fetch_last_prices(tickers: dict[str, str]) -> tuple[dict[str, float], list[str]]:
    """Sweep {symbol: yfinance_ticker} → ({symbol: last_price}, [failed]).
    Throttling makes a 15-name sweep take ~10–20s — deliberate, not a bug."""
    prices: dict[str, float] = {}
    failed: list[str] = []
    for sym, tk in tickers.items():
        px = fetch_last_price(tk)
        if px is None:
            failed.append(sym)
        else:
            prices[sym] = px
    return prices, failed
