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
import hmac
import math
import os
import sqlite3
import time
from datetime import date

from dotenv import load_dotenv
from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)

load_dotenv()

# Shared constants + read-only helpers live in web_common (single source; also
# used by views_research).
from web_common import (BASE_DIR, DATA_DIR, INTRADAY_DB, OPTIONS_DB,
                        STARTING_CAPITAL, login_required,
                        last_close, live_price, warm_prices, paper_db,
                        BOOK_STATUS, status_for, sparkline_svg)

app = Flask(__name__)
# Stable secret from .env (DASHBOARD_SECRET_KEY) so sessions survive restarts;
# falls back to a per-process random key if it isn't set (you'll just re-login).
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY") or os.urandom(24)

# Hot-reload templates so edits show on refresh without a full server restart.
# (Prevents the "new template + stale Python" 500s seen during development.)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# Extended features (analytics, journal, alerts, simulated order ticket, exports).
# All writes are confined to isolated feature DBs; nothing places a live order.
from features import bp as features_bp  # noqa: E402
app.register_blueprint(features_bp)

# Research / summary pages (Home, P&L, tear sheets, factors, portfolio, risk,
# attribution, data quality, backtests) — registered with their ORIGINAL endpoint
# names so the templates' url_for(...) calls are unchanged.
import views_research  # noqa: E402
views_research.register(app)


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    configured = bool(os.getenv("DASHBOARD_PASSWORD"))
    error = None
    if request.method == "POST":
        if not configured:
            error = "No password set. Add DASHBOARD_PASSWORD=... to .env and restart."
        elif hmac.compare_digest(request.form.get("password", ""),
                                 os.getenv("DASHBOARD_PASSWORD", "")):
            session["authed"] = True
            return redirect(url_for("command"))   # Quiet Terminal is the landing page
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

    palette = ["#d2a95e", "#41cd8b", "#7aa2f7", "#f0655b"]

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

    # Attach an inline sparkline + status pill to each strategy's summary row.
    spark_by = {ser["name"]: ser["data"] for ser in series}
    for so in summary:
        so["spark_svg"] = sparkline_svg(spark_by.get(so["strategy"], []), color=so["color"])
        so["pill"] = BOOK_STATUS.get(status_for(None, so["strategy"]), BOOK_STATUS["paper"])

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


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Keep the price cache warm in the background so pages don't wait on yfinance.
    # Watches the low-vol book's current holdings; started only here (when actually
    # serving), never during tests / smoke / scheduled imports.
    from web_common import start_price_refresher

    def _watched_symbols():
        c = paper_db()
        if c is None:
            return []
        try:
            return [r["symbol"] for r in c.execute("SELECT symbol FROM positions")]
        finally:
            c.close()

    start_price_refresher(_watched_symbols, interval=180)

    print("\n  tradebot dashboard →  http://127.0.0.1:5050\n")
    app.run(host="127.0.0.1", port=5050, debug=False)
