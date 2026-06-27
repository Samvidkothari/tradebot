"""
web_common.py — shared constants + helpers for the dashboard and its view modules.

Extracted from dashboard.py so the route modules (dashboard.py market views +
views_research.py) share one definition of the paths, capital, auth decorator,
and the read-only price/DB helpers — without a circular import. READ-ONLY; no
order-placement code.
"""

import csv
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path

from flask import redirect, session, url_for

from config import PAPER_CAPITAL
from paper_trader import fetch_live

BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
RESULTS_DIR  = BASE_DIR / "results"
DB_PATH      = BASE_DIR / "portfolio.db"
INTRADAY_DB  = BASE_DIR / "intraday.db"
OPTIONS_DB   = BASE_DIR / "options.db"

STARTING_CAPITAL = PAPER_CAPITAL  # single source of truth in config.py


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ── Read-only price / DB helpers (paper trader + P&L) ─────────────────────────

def last_close(symbol):
    """Most recent close from data/<symbol>.csv, or None if unavailable."""
    fp = DATA_DIR / f"{symbol}.csv"
    if not fp.exists():
        return None
    last = None
    with open(fp, newline="") as f:
        for last in csv.DictReader(f):
            pass
    try:
        return float(last["close"]) if last else None
    except (KeyError, ValueError):
        return None


PRICE_TTL    = 300            # seconds a fetched price stays fresh (5 min)
_price_cache = {}             # symbol -> (price, fetched_at)


def live_price(symbol):
    """Live last price (yfinance, SYMBOL.NS), memoised for PRICE_TTL seconds and
    falling back to the latest CSV close if the fetch fails or returns nothing."""
    now = time.time()
    hit = _price_cache.get(symbol)
    if hit is not None and now - hit[1] < PRICE_TTL:
        return hit[0]
    try:
        ser = fetch_live(f"{symbol}.NS")
        if ser is not None and not ser.empty:
            px = float(ser.iloc[-1])
            _price_cache[symbol] = (px, now)
            return px
    except Exception:
        pass
    return last_close(symbol)


def warm_prices(symbols):
    """Fetch all not-yet-fresh symbols concurrently so the first page load
    pays one round-trip instead of N sequential ones."""
    now = time.time()
    stale = [s for s in symbols
             if s not in _price_cache or now - _price_cache[s][1] >= PRICE_TTL]
    if not stale:
        return
    with ThreadPoolExecutor(max_workers=min(8, len(stale))) as ex:
        ex.map(live_price, stale)


def paper_db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # read-only
    conn.row_factory = sqlite3.Row
    return conn
