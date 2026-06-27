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
import json
import math
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
from digest import build_digest

load_dotenv()

BASE_DIR     = Path(__file__).parent
DATA_DIR     = BASE_DIR / "data"
RESULTS_DIR  = BASE_DIR / "results"
DB_PATH      = BASE_DIR / "portfolio.db"
INTRADAY_DB  = BASE_DIR / "intraday.db"
OPTIONS_DB   = BASE_DIR / "options.db"

STARTING_CAPITAL = 1_000_000  # must match paper_trader.py / intraday_sim.py

app = Flask(__name__)
app.secret_key = os.urandom(24)  # sessions reset on restart — that's fine

# Extended features (analytics, journal, alerts, simulated order ticket, exports).
# All writes are confined to isolated feature DBs; nothing places a live order.
from features import bp as features_bp  # noqa: E402
app.register_blueprint(features_bp)


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
            return redirect(url_for("home"))
        else:
            error = "Wrong password."
    return render_template("login.html", error=error, configured=configured)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/home")
@login_required
def home():
    return render_template("home.html", active="home", digest=build_digest())


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
    empty = dict(days=[], trades=[], chart=None, equity=0, cum_net=0, ret_pct=0,
                 n_trades=0, win_rate=None, by_symbol=[], by_side=[],
                 strategies=[], selected=None)
    conn = intraday_db()
    if conn is None:
        return render_template(
            "intraday.html", active="intraday",
            error="intraday.db not found — run intraday_sim.py first.", **empty)

    # Available strategy books; the page shows one at a time (selector at top).
    strategies = [r["strategy"] for r in
                  conn.execute("SELECT strategy FROM account ORDER BY strategy")]
    if not strategies:
        conn.close()
        return render_template("intraday.html", active="intraday",
                               error="No strategy books yet — run intraday_sim.py.",
                               **empty)
    selected = request.args.get("strategy")
    if selected not in strategies:
        selected = strategies[0]

    cash = conn.execute("SELECT cash FROM account WHERE strategy = ?",
                        (selected,)).fetchone()
    equity = cash["cash"] if cash else 0.0

    days = [dict(d) for d in conn.execute(
        "SELECT trade_date, n_trades, gross_pnl, costs, net_pnl FROM days "
        "WHERE strategy = ? ORDER BY trade_date DESC", (selected,))]

    trades = [dict(t) for t in conn.execute(
        "SELECT trade_date, symbol, side, entry_time, entry_px, exit_time, "
        "exit_px, qty, net_pnl, exit_reason FROM trades WHERE strategy = ? "
        "ORDER BY id DESC LIMIT 100", (selected,))]

    agg = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(net_pnl),0) net, "
        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins "
        "FROM trades WHERE strategy = ?", (selected,)).fetchone()
    n_trades = agg["n"]
    cum_net  = agg["net"]
    win_rate = (agg["wins"] / n_trades * 100) if n_trades else None

    # Per-symbol and long-vs-short breakdowns (win% computed in the template)
    by_symbol = [dict(r) for r in conn.execute(
        "SELECT symbol, COUNT(*) n, "
        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins, "
        "COALESCE(SUM(net_pnl),0) net FROM trades WHERE strategy = ? "
        "GROUP BY symbol ORDER BY net DESC", (selected,))]
    by_side = [dict(r) for r in conn.execute(
        "SELECT side, COUNT(*) n, "
        "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins, "
        "COALESCE(SUM(net_pnl),0) net FROM trades WHERE strategy = ? "
        "GROUP BY side ORDER BY side", (selected,))]

    # Cumulative net P&L by day (oldest → newest) for the chart
    rows = conn.execute(
        "SELECT trade_date, net_pnl FROM days WHERE strategy = ? "
        "ORDER BY trade_date", (selected,)).fetchall()
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
        by_symbol=by_symbol, by_side=by_side,
        strategies=strategies, selected=selected)


@app.route("/intraday/compare")
@login_required
def intraday_compare():
    conn = intraday_db()
    if conn is None:
        return render_template(
            "intraday_compare.html", active="intraday",
            error="intraday.db not found — run intraday_sim.py first.",
            summary=[], rows=[], chart=None, strategies=[])

    strategies = [r["strategy"] for r in
                  conn.execute("SELECT strategy FROM account ORDER BY strategy")]
    if not strategies:
        conn.close()
        return render_template(
            "intraday_compare.html", active="intraday",
            error="No strategy books yet — run intraday_sim.py.",
            summary=[], rows=[], chart=None, strategies=[])

    palette = ["#7c8cff", "#caa45d", "#4cc38a", "#f0716a"]

    # Per-strategy headline metrics.
    summary = []
    for i, s in enumerate(strategies):
        cash = conn.execute("SELECT cash FROM account WHERE strategy = ?",
                            (s,)).fetchone()
        equity = cash["cash"] if cash else 0.0
        agg = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(net_pnl),0) net, "
            "COALESCE(SUM(costs),0) costs, "
            "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins "
            "FROM trades WHERE strategy = ?", (s,)).fetchone()
        summary.append({
            "strategy": s, "color": palette[i % len(palette)],
            "equity": equity, "cum_net": agg["net"], "costs": agg["costs"],
            "n_trades": agg["n"],
            "win_rate": (agg["wins"] / agg["n"] * 100) if agg["n"] else None,
            "ret_pct": (equity - STARTING_CAPITAL) / STARTING_CAPITAL * 100,
        })

    # Per-day net by strategy, aligned on the union of all dates.
    all_dates = [r["trade_date"] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM days ORDER BY trade_date")]
    daynet = {s: {} for s in strategies}
    for r in conn.execute("SELECT strategy, trade_date, net_pnl FROM days"):
        daynet[r["strategy"]][r["trade_date"]] = r["net_pnl"] or 0.0

    # Overlaid cumulative-net curves (carry forward across days with no trades).
    series = []
    for i, s in enumerate(strategies):
        cum, vals = 0.0, []
        for d in all_dates:
            cum += daynet[s].get(d, 0.0)
            vals.append(round(cum, 2))
        series.append({"name": s, "data": vals, "color": palette[i % len(palette)]})
    chart = {"labels": all_dates, "series": series} if all_dates else None

    # Per-day table (newest first): each strategy's net for that date.
    rows = [{"date": d, "nets": {s: daynet[s].get(d) for s in strategies}}
            for d in reversed(all_dates)]

    conn.close()
    return render_template(
        "intraday_compare.html", active="intraday", error=None,
        summary=summary, rows=rows, chart=chart, strategies=strategies)


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


# ── Options payoff / pricing tool (model-priced) ──────────────────────────────

def realized_vol(symbol, window=30):
    """Annualised realized volatility from the last `window` daily log-returns
    in data/<symbol>.csv. Used as an implied-vol PROXY (no real chain data)."""
    fp = DATA_DIR / f"{symbol}.csv"
    if not fp.exists():
        return None
    closes = []
    with open(fp, newline="") as f:
        for row in csv.DictReader(f):
            try:
                closes.append(float(row["close"]))
            except (KeyError, ValueError):
                continue
    closes = closes[-(window + 1):]
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var  = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def option_inputs():
    """{symbol: {spot, vol%}} defaults for every underlying with a CSV."""
    out = {}
    for s in chart_symbols():
        spot = last_close(s)
        if spot:
            v = realized_vol(s)
            out[s] = {"spot": round(spot, 2), "vol": round((v or 0.25) * 100, 1)}
    return out


@app.route("/options")
@login_required
def options():
    inputs = option_inputs()
    syms = list(inputs.keys())
    return render_template("options.html", active="options",
                           inputs=inputs, symbols=syms,
                           default=(syms[0] if syms else None))


@app.route("/options/book")
@login_required
def options_book():
    if not OPTIONS_DB.exists():
        return render_template(
            "options_book.html", active="options",
            error="options.db not found — run options_sim.py first.",
            open_pos=None, closed=[], marks=None, had_event=False,
            equity=0, realized=0, unrealized=0, started=STARTING_CAPITAL,
            n_closed=0, wins=0, stops=0)

    conn = sqlite3.connect(f"file:{OPTIONS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
    realized = cash["cash"] - STARTING_CAPITAL if cash else 0.0
    equity_real = cash["cash"] if cash else STARTING_CAPITAL

    cyc = conn.execute("SELECT * FROM cycles WHERE status = 'open'").fetchone()
    open_pos, unrealized, marks = None, 0.0, None
    if cyc:
        last = conn.execute(
            "SELECT * FROM marks WHERE cycle_id = ? ORDER BY mark_date DESC LIMIT 1",
            (cyc["id"],)).fetchone()
        unrealized = last["open_pnl"] if last else 0.0
        dte = (date.fromisoformat(cyc["expiry"]) - date.today()).days
        open_pos = {**dict(cyc), "open_pnl": unrealized,
                    "spot": (last["spot"] if last else None), "dte": dte}
        m = conn.execute(
            "SELECT mark_date, open_pnl FROM marks WHERE cycle_id = ? "
            "ORDER BY mark_date", (cyc["id"],)).fetchall()
        if len(m) >= 2:
            marks = {"labels": [r["mark_date"] for r in m],
                     "values": [r["open_pnl"] for r in m]}

    closed = [dict(c) for c in conn.execute(
        "SELECT * FROM cycles WHERE status = 'closed' ORDER BY id DESC")]
    agg = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN settle_pnl>0 THEN 1 ELSE 0 END) wins, "
        "SUM(CASE WHEN close_reason='STOP' THEN 1 ELSE 0 END) stops "
        "FROM cycles WHERE status='closed'").fetchone()
    had_event = conn.execute(
        "SELECT 1 FROM marks WHERE ABS(daily_move) >= 0.04 LIMIT 1").fetchone() is not None
    conn.close()

    return render_template(
        "options_book.html", active="options", error=None,
        open_pos=open_pos, closed=closed, marks=marks, had_event=had_event,
        equity=equity_real + unrealized, realized=realized, unrealized=unrealized,
        started=STARTING_CAPITAL, n_closed=agg["n"],
        wins=agg["wins"] or 0, stops=agg["stops"] or 0,
        condor=_condor_summary())


def _condor_summary():
    """Compact defined-risk iron-condor read for the Options book page (read-only)."""
    db = BASE_DIR / "condor.db"
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
        realized = (cash["cash"] - STARTING_CAPITAL) if cash else 0.0
        cyc = conn.execute("SELECT * FROM cycles WHERE status='open'").fetchone()
        open_pos = None
        if cyc:
            last = conn.execute(
                "SELECT open_pnl, spot FROM marks WHERE cycle_id=? "
                "ORDER BY mark_date DESC LIMIT 1", (cyc["id"],)).fetchone()
            dte = (date.fromisoformat(cyc["expiry"]) - date.today()).days
            open_pos = {**dict(cyc), "dte": dte,
                        "open_pnl": last["open_pnl"] if last else 0.0,
                        "spot": last["spot"] if last else None}
        n_closed = conn.execute(
            "SELECT COUNT(*) n FROM cycles WHERE status='closed'").fetchone()["n"]
        had_event = conn.execute(
            "SELECT 1 FROM marks WHERE ABS(daily_move)>=0.04 LIMIT 1").fetchone() is not None
        return {"realized": realized, "open": open_pos,
                "n_closed": n_closed, "had_event": had_event}
    finally:
        conn.close()


# ── Consolidated P&L (every paper book, read-only) ────────────────────────────

def _pnl_lowvol():
    """Low-vol equity book: realised from SELL fills + unrealised marked to the
    latest live price (falls back to last CSV close)."""
    conn = paper_db()
    if conn is None:
        return None
    positions = conn.execute("SELECT symbol, qty, avg_price FROM positions").fetchall()
    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl),0) r FROM fills WHERE side='SELL'").fetchone()["r"]
    conn.close()
    warm_prices([p["symbol"] for p in positions])
    unreal = 0.0
    for p in positions:
        px = live_price(p["symbol"]) or p["avg_price"]
        unreal += (px - p["avg_price"]) * p["qty"]
    return {"book": "Low-vol equity", "realised": realised, "unrealised": unreal,
            "status": "active", "note": "marked to latest price · proven strategy"}


def _pnl_option_book(db_name, label, note):
    """One options book: realised = cash − start; unrealised = latest open mark."""
    p = BASE_DIR / db_name
    if not p.exists():
        return None
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
        realised = (cash["cash"] - STARTING_CAPITAL) if cash else 0.0
        cyc = conn.execute("SELECT id FROM cycles WHERE status='open'").fetchone()
        unreal = 0.0
        if cyc:
            m = conn.execute("SELECT open_pnl FROM marks WHERE cycle_id=? "
                             "ORDER BY mark_date DESC LIMIT 1", (cyc["id"],)).fetchone()
            unreal = m["open_pnl"] if m else 0.0
        return {"book": label, "realised": realised, "unrealised": unreal,
                "status": "active", "note": note}
    finally:
        conn.close()


def _pnl_intraday():
    """Retired ORB + VWAP books — realised, frozen as evidence."""
    if not INTRADAY_DB.exists():
        return []
    conn = sqlite3.connect(f"file:{INTRADAY_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [{"book": f"Intraday {r['strategy']}",
                 "realised": r["cash"] - STARTING_CAPITAL, "unrealised": 0.0,
                 "status": "retired", "note": "frozen evidence"}
                for r in conn.execute("SELECT strategy, cash FROM account ORDER BY strategy")]
    finally:
        conn.close()


def _pnl_total(sel):
    return {"realised": sum(r["realised"] for r in sel),
            "unrealised": sum(r["unrealised"] for r in sel),
            "total": sum(r["total"] for r in sel)}


@app.route("/pnl")
@login_required
def pnl():
    rows = [r for r in (
        _pnl_lowvol(),
        _pnl_option_book("options.db", "Options strangle", "mark-to-model · inconclusive"),
        _pnl_option_book("condor.db", "Options condor", "mark-to-model · inconclusive"),
    ) if r]
    rows += _pnl_intraday()
    for r in rows:
        r["total"] = r["realised"] + r["unrealised"]
    active = [r for r in rows if r["status"] == "active"]
    retired = [r for r in rows if r["status"] == "retired"]
    return render_template(
        "pnl.html", active="pnl", active_rows=active, retired_rows=retired,
        active_total=_pnl_total(active), retired_total=_pnl_total(retired),
        grand=_pnl_total(rows), started=STARTING_CAPITAL)


# ── Research tear sheets (results/tearsheets.json) ────────────────────────────

def _research_json(filename, runner):
    """Load results/<filename>; return (data, error_message). Shared by every
    research page so the file-exists / json-load / error wiring lives in one place."""
    fp = RESULTS_DIR / filename
    if not fp.exists():
        return None, f"No data yet — run `python {runner}`."
    return json.loads(fp.read_text()), None


@app.route("/tearsheet")
@login_required
def tearsheet():
    data, error = _research_json("tearsheets.json", "tearsheet.py")
    if error:
        return render_template("tearsheet.html", active="tearsheet", error=error,
                               generated=None, equity=[], options=[], metric_rows=[])
    strats = data.get("strategies", {})
    equity = [s for s in strats.values()
              if s.get("kind") == "equity" and s.get("sufficient")]
    options = [s for s in strats.values() if s.get("kind") == "options"]
    metric_rows = [
        ("CAGR", "cagr", True), ("Total return", "total_return", True),
        ("Max drawdown", "max_drawdown", True), ("Annualised vol", "annual_vol", True),
        ("Sharpe", "sharpe", False), ("Sortino", "sortino", False),
        ("Calmar", "calmar", False), ("Recovery factor", "recovery_factor", False),
        ("Profit factor", "profit_factor", False), ("Win rate", "win_rate", True),
        ("Beta vs NIFTY", "beta", False), ("Alpha vs NIFTY", "alpha", True),
        ("Information ratio", "information_ratio", False),
    ]
    return render_template("tearsheet.html", active="tearsheet", error=None,
                           generated=data.get("generated"), equity=equity,
                           options=options, metric_rows=metric_rows,
                           regime=data.get("regime"))


@app.route("/data-quality")
@login_required
def data_quality_view():
    data, error = _research_json("data_quality.json", "data_quality.py")
    return render_template("data_quality.html", active="data_quality",
                           data=data, error=error)


@app.route("/attribution")
@login_required
def attribution_view():
    data, error = _research_json("attribution.json", "attribution_report.py")
    if error:
        return render_template("attribution.html", active="attribution",
                               data=None, error=error)
    # Pre-sort holding contributions per strategy for display.
    for s in data.get("strategies", {}).values():
        bs = s["holdings"]["by_symbol"]
        ordered = sorted(bs.items(), key=lambda kv: kv[1], reverse=True)
        s["_top"] = ordered[:8]
        s["_bottom"] = ordered[-5:][::-1]
        s["_sectors"] = sorted(s["holdings"]["by_sector"].items(),
                               key=lambda kv: kv[1], reverse=True)
    return render_template("attribution.html", active="attribution", error=None, data=data)


@app.route("/risk")
@login_required
def risk_view():
    data, error = _research_json("risk.json", "risk_report.py")
    return render_template("risk.html", active="risk", data=data, error=error)


@app.route("/portfolio-analysis")
@login_required
def portfolio_analysis():
    data, error = _research_json("portfolio.json", "portfolio_analyzer.py")
    return render_template("portfolio_analysis.html", active="portfolio_analysis",
                           data=data, error=error)


@app.route("/factors")
@login_required
def factors_view():
    data, error = _research_json("factors.json", "factor_report.py")
    return render_template("factors.html", active="factors", data=data, error=error)


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
