"""
dashboard.py — Local web dashboard for the tradebot project.

A SaaS-style UI over the existing project, strictly READ-ONLY:
  • Live Portfolio  — real holdings & positions from Kite (via kite_client)
  • Paper Trader    — simulated portfolio state from portfolio.db
  • Backtests       — rendered reports from results/*.md

It can read your account data but contains NO order-placement code.

Run:
    python dashboard.py
then open  http://127.0.0.1:5050  in your browser.

Login password comes from DASHBOARD_PASSWORD in .env (add a line like
DASHBOARD_PASSWORD=something-you-choose). The server binds to 127.0.0.1
only, so it is reachable just from this machine.
"""

import csv
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)

from paper_trader import fetch_live

load_dotenv()

BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
RESULTS_DIR  = BASE_DIR / "results"
DB_PATH      = BASE_DIR / "portfolio.db"
INTRADAY_DB  = BASE_DIR / "intraday.db"

STARTING_CAPITAL = 1_000_000  # must match paper_trader.py / intraday_sim.py

app = Flask(__name__)
app.secret_key = os.urandom(24)  # sessions reset on restart — that's fine


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    configured = bool(os.getenv("DASHBOARD_PASSWORD"))
    error = None
    if request.method == "POST":
        if not configured:
            error = "No password set. Add DASHBOARD_PASSWORD=... to .env and restart."
        elif request.form.get("password") == os.getenv("DASHBOARD_PASSWORD"):
            session["authed"] = True
            return redirect(url_for("overview"))
        else:
            error = "Wrong password."
    return render_template("login.html", error=error, configured=configured)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Live portfolio (Kite) ─────────────────────────────────────────────────────

def get_kite():
    """
    Connect via the shared kite_client module.
    kite_client.load_kite() raises SystemExit with a friendly message when the
    token is missing/stale — we catch that and surface it in the UI instead.
    Returns (kite, error_message).
    """
    try:
        from kite_client import load_kite
        return load_kite(), None
    except SystemExit as e:
        return None, str(e).strip()
    except Exception as e:
        return None, f"Could not connect to Kite: {e}"


@app.route("/")
@login_required
def overview():
    kite, err = get_kite()
    holdings, positions = [], []
    total_pnl = total_invested = total_current = 0.0

    if kite is not None:
        try:
            for h in kite.holdings():
                qty, avg, ltp = h["quantity"], h["average_price"], h["last_price"]
                pnl = (ltp - avg) * qty
                pct = ((ltp - avg) / avg * 100) if avg else 0.0
                total_pnl      += pnl
                total_invested += avg * qty
                total_current  += ltp * qty
                holdings.append({
                    "symbol": h["tradingsymbol"], "qty": qty, "avg": avg,
                    "ltp": ltp, "pnl": pnl, "pct": pct,
                })
            net = kite.positions().get("net", [])
            positions = [{
                "symbol": p["tradingsymbol"], "qty": p["quantity"],
                "avg": p["average_price"], "ltp": p["last_price"],
                "pnl": p.get("pnl", 0.0),
            } for p in net if p["quantity"] != 0]
        except Exception as e:
            err = f"Kite API error: {e}"

    holdings.sort(key=lambda x: x["pnl"], reverse=True)
    total_pct = (total_pnl / total_invested * 100) if total_invested else 0.0
    return render_template(
        "overview.html", active="overview", error=err,
        holdings=holdings, positions=positions,
        total_pnl=total_pnl, total_pct=total_pct,
        total_invested=total_invested, total_current=total_current,
    )


# ── Paper trader (portfolio.db) ───────────────────────────────────────────────

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


@app.route("/paper")
@login_required
def paper():
    conn = paper_db()
    if conn is None:
        return render_template("paper.html", active="paper",
                               error="portfolio.db not found — run paper_trader.py first.",
                               positions=[], fills=[], chart=None,
                               cash=0, equity=0, realised=0, ret_pct=0)

    cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
    cash = cash["cash"] if cash else 0.0

    rows = conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall()
    warm_prices([r["symbol"] for r in rows])   # concurrent prefetch into cache

    positions = []
    pos_value = 0.0
    for row in rows:
        ltp   = live_price(row["symbol"])
        value = (ltp or row["avg_price"]) * row["qty"]
        pnl   = ((ltp - row["avg_price"]) * row["qty"]) if ltp else None
        pos_value += value
        positions.append({
            "symbol": row["symbol"], "qty": row["qty"], "avg": row["avg_price"],
            "ltp": ltp, "value": value, "pnl": pnl, "opened": row["opened"],
        })

    fills = [dict(r) for r in conn.execute(
        "SELECT run_date, symbol, side, qty, price, cost, realised_pnl "
        "FROM fills ORDER BY id DESC LIMIT 100")]

    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl), 0) AS s FROM fills").fetchone()["s"]

    # Cumulative realised P&L by date — for the chart
    chart_rows = conn.execute(
        "SELECT run_date, SUM(realised_pnl) AS pnl FROM fills "
        "GROUP BY run_date ORDER BY run_date").fetchall()
    cum, labels, values = 0.0, [], []
    for r in chart_rows:
        cum += r["pnl"] or 0.0
        labels.append(r["run_date"])
        values.append(round(cum, 2))
    chart = {"labels": labels, "values": values} if labels else None

    conn.close()
    equity  = cash + pos_value
    ret_pct = (equity - STARTING_CAPITAL) / STARTING_CAPITAL * 100
    return render_template(
        "paper.html", active="paper", error=None,
        cash=cash, equity=equity, realised=realised, ret_pct=ret_pct,
        positions=positions, fills=fills, chart=chart,
    )


# ── Intraday paper sim (intraday.db) ──────────────────────────────────────────

def intraday_db():
    if not INTRADAY_DB.exists():
        return None
    conn = sqlite3.connect(f"file:{INTRADAY_DB}?mode=ro", uri=True)  # read-only
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/intraday")
@login_required
def intraday():
    conn = intraday_db()
    if conn is None:
        return render_template(
            "intraday.html", active="intraday",
            error="intraday.db not found — run intraday_sim.py first.",
            days=[], trades=[], chart=None,
            equity=0, cum_net=0, ret_pct=0, n_trades=0, win_rate=None,
            by_symbol=[], by_side=[])

    cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
    equity = cash["cash"] if cash else 0.0

    days = [dict(d) for d in conn.execute(
        "SELECT trade_date, n_trades, gross_pnl, costs, net_pnl "
        "FROM days ORDER BY trade_date DESC")]

    trades = [dict(t) for t in conn.execute(
        "SELECT trade_date, symbol, side, entry_time, entry_px, exit_time, "
        "exit_px, qty, net_pnl, exit_reason FROM trades ORDER BY id DESC LIMIT 100")]

    agg = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(net_pnl),0) net, "
        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins FROM trades").fetchone()
    n_trades = agg["n"]
    cum_net  = agg["net"]
    win_rate = (agg["wins"] / n_trades * 100) if n_trades else None

    # Per-symbol and long-vs-short breakdowns (win% computed in the template)
    by_symbol = [dict(r) for r in conn.execute(
        "SELECT symbol, COUNT(*) n, "
        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins, "
        "COALESCE(SUM(net_pnl),0) net FROM trades "
        "GROUP BY symbol ORDER BY net DESC")]
    by_side = [dict(r) for r in conn.execute(
        "SELECT side, COUNT(*) n, "
        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins, "
        "COALESCE(SUM(net_pnl),0) net FROM trades "
        "GROUP BY side ORDER BY side")]

    # Cumulative net P&L by day (oldest → newest) for the chart
    rows = conn.execute(
        "SELECT trade_date, net_pnl FROM days ORDER BY trade_date").fetchall()
    cum, labels, values = 0.0, [], []
    for r in rows:
        cum += r["net_pnl"] or 0.0
        labels.append(r["trade_date"])
        values.append(round(cum, 2))
    chart = {"labels": labels, "values": values} if labels else None

    conn.close()
    ret_pct = (equity - STARTING_CAPITAL) / STARTING_CAPITAL * 100
    return render_template(
        "intraday.html", active="intraday", error=None,
        days=days, trades=trades, chart=chart,
        equity=equity, cum_net=cum_net, ret_pct=ret_pct,
        n_trades=n_trades, win_rate=win_rate,
        by_symbol=by_symbol, by_side=by_side)


# ── Candlestick charts (data/*.csv) ───────────────────────────────────────────

def chart_symbols():
    """All symbols with a CSV in data/, NIFTY50 index first."""
    syms = sorted(fp.stem for fp in DATA_DIR.glob("*.csv"))
    if "NIFTY50" in syms:
        syms.remove("NIFTY50")
        syms.insert(0, "NIFTY50")
    return syms


@app.route("/charts")
@app.route("/charts/<symbol>")
@login_required
def charts(symbol=None):
    syms = chart_symbols()
    if not syms:
        return render_template("charts.html", active="charts",
                               symbols=[], symbol=None)
    if symbol is None:
        symbol = syms[0]
    if symbol not in syms:
        abort(404)
    return render_template("charts.html", active="charts",
                           symbols=syms, symbol=symbol)


@app.route("/api/candles/<symbol>")
@login_required
def api_candles(symbol):
    if symbol not in chart_symbols():   # validates against real files only
        abort(404)
    candles = []
    with open(DATA_DIR / f"{symbol}.csv", newline="") as f:
        for row in csv.DictReader(f):
            try:
                candles.append({
                    "time":   row["date"],
                    "open":   round(float(row["open"]), 2),
                    "high":   round(float(row["high"]), 2),
                    "low":    round(float(row["low"]), 2),
                    "close":  round(float(row["close"]), 2),
                    "volume": int(float(row["volume"])),
                })
            except (KeyError, ValueError):
                continue   # skip malformed rows
    return jsonify(candles)


# ── Backtest reports (results/*.md) ───────────────────────────────────────────

@app.route("/backtests")
@login_required
def backtests():
    reports = sorted(fp.name for fp in RESULTS_DIR.glob("*.md")) \
        if RESULTS_DIR.exists() else []
    return render_template("backtests.html", active="backtests",
                           reports=reports, current=None, content=None)


@app.route("/backtests/<name>")
@login_required
def backtest_view(name):
    # Only allow simple .md filenames that actually exist in results/
    if "/" in name or "\\" in name or not name.endswith(".md"):
        abort(404)
    fp = RESULTS_DIR / name
    if not fp.exists():
        abort(404)
    reports = sorted(f.name for f in RESULTS_DIR.glob("*.md"))
    return render_template("backtests.html", active="backtests",
                           reports=reports, current=name,
                           content=fp.read_text())


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  tradebot dashboard →  http://127.0.0.1:5050\n")
    app.run(host="127.0.0.1", port=5050, debug=False)
