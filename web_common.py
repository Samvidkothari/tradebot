"""
web_common.py — shared constants + helpers for the dashboard and its view modules.

Extracted from dashboard.py so the route modules (dashboard.py market views +
views_research.py) share one definition of the paths, capital, auth decorator,
and the read-only price/DB helpers — without a circular import. READ-ONLY; no
order-placement code.
"""

import csv
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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


PRICE_TTL     = 300           # seconds a fetched price stays fresh (5 min)
FETCH_TIMEOUT = 4             # hard cap (s) on a single upstream price fetch
_price_cache  = {}            # symbol -> (price, fetched_at)
_log          = logging.getLogger("tradebot.price")

# Dedicated pool so a single fetch can be bounded with a timeout. A hung
# yfinance call is abandoned after FETCH_TIMEOUT and we fall back to the CSV
# close, so the request thread is never blocked on a slow/unreachable upstream.
_fetch_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="price-fetch")


def _fetch_last(symbol):
    ser = fetch_live(f"{symbol}.NS")
    if ser is not None and not ser.empty:
        return float(ser.iloc[-1])
    return None


def live_price(symbol):
    """Live last price (yfinance, SYMBOL.NS), memoised for PRICE_TTL seconds.
    The upstream fetch is bounded by FETCH_TIMEOUT; on timeout, error, or empty
    result we fall back to the latest CSV close so a slow upstream never stalls
    page rendering."""
    now = time.time()
    hit = _price_cache.get(symbol)
    if hit is not None and now - hit[1] < PRICE_TTL:
        return hit[0]
    try:
        px = _fetch_pool.submit(_fetch_last, symbol).result(timeout=FETCH_TIMEOUT)
        if px is not None:
            _price_cache[symbol] = (px, now)
            return px
    except FutureTimeout:
        _log.warning("price fetch for %s timed out after %ss; using CSV close",
                     symbol, FETCH_TIMEOUT)
    except Exception as e:
        _log.warning("price fetch for %s failed (%s); using CSV close", symbol, e)
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


_refresher_started = False


def start_price_refresher(symbols_fn, interval=180):
    """Warm the price cache in the background so requests almost always hit a
    fresh value instead of waiting on yfinance. `symbols_fn` is called each tick
    to get the current set of symbols to keep warm (so it tracks position
    changes). Idempotent; the thread is a daemon so it never blocks shutdown.

    Started only from dashboard.py's __main__ block — NOT at import — so the test
    client, smoke test, and scheduled tasks never spin up a background loop."""
    global _refresher_started
    if _refresher_started:
        return
    _refresher_started = True

    def _loop():
        while True:
            try:
                syms = symbols_fn() or []
                if syms:
                    warm_prices(syms)
            except Exception as e:                       # never let the loop die
                _log.warning("price refresher tick failed: %s", e)
            time.sleep(interval)

    threading.Thread(target=_loop, name="price-refresher", daemon=True).start()
    _log.info("price refresher started (every %ss)", interval)


def ro_db(path):
    """Open a SQLite DB read-only with a Row factory, or None if it's missing.
    Canonical helper for every read-only ledger access across the app."""
    path = Path(path)
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def rw_db(path, schema=None):
    """Open (creating if needed) a writable SQLite DB with a Row factory, applying
    an idempotent schema if given. Used only by the isolated feature DBs."""
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    if schema:
        c.executescript(schema)
        c.commit()
    return c


def paper_db():
    """Read-only handle to the low-vol equity ledger (portfolio.db)."""
    return ro_db(DB_PATH)


# ── Presentation helpers (status taxonomy + inline-SVG sparklines) ─────────────

# How each book is labelled in the UI. All are paper/simulated; the tag conveys
# the book's ROLE, not live trading. (Nothing here ever places an order.)
BOOK_STATUS = {
    "live":    {"label": "LIVE",    "cls": "up"},     # proven/active strategy (low-vol)
    "watch":   {"label": "WATCH",   "cls": "warnpill"},  # awaiting a vol event (options)
    "paper":   {"label": "PAPER",   "cls": "flat"},
    "retired": {"label": "RETIRED", "cls": "flat"},   # frozen as evidence
}


def status_for(kind=None, name=""):
    """Map a strategy kind / name to a UI status key."""
    n = (name or "").lower()
    if (kind or "").lower() == "options" or "strangle" in n or "condor" in n:
        return "watch"
    if "intraday" in n or "orb" in n or "vwap" in n:
        return "retired"
    if (kind or "").lower() in ("equity", "lowvol") or "low-vol" in n or "lowvol" in n:
        return "live"
    return "paper"


def sparkline_svg(values, color=None, width=92, height=24, stroke=1.6, fill=True):
    """Return a self-contained inline-SVG sparkline string for a numeric series.
    Colour auto-derives from the trend (green up / red down) unless overridden.
    Renders nothing useful for <2 points (returns an em-dash span)."""
    vals = [v for v in (values or []) if v is not None]
    if len(vals) < 2:
        return '<span style="color:var(--faint)">—</span>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pad = 2.0
    iw, ih = width - 2 * pad, height - 2 * pad

    def xy(i, v):
        x = pad + (i / (n - 1)) * iw
        y = pad + (1 - (v - lo) / rng) * ih
        return f"{x:.1f},{y:.1f}"

    pts = " ".join(xy(i, v) for i, v in enumerate(vals))
    if color is None:
        color = "var(--green)" if vals[-1] >= vals[0] else "var(--red)"
    uid = abs(hash((tuple(vals), color))) % 100000
    area = ""
    if fill:
        area = (f'<defs><linearGradient id="sp{uid}" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0%" stop-color="{color}" stop-opacity="0.28"/>'
                f'<stop offset="100%" stop-color="{color}" stop-opacity="0"/>'
                f'</linearGradient></defs>'
                f'<polygon fill="url(#sp{uid})" stroke="none" '
                f'points="{pad:.1f},{height - pad:.1f} {pts} {width - pad:.1f},{height - pad:.1f}"/>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'style="display:block" preserveAspectRatio="none">{area}'
            f'<polyline fill="none" stroke="{color}" stroke-width="{stroke}" '
            f'stroke-linejoin="round" stroke-linecap="round" points="{pts}"/></svg>')
