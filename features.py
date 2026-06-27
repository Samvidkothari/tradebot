"""
features.py — extended dashboard features for the tradebot console.

Everything here is additive and registered on the existing Flask app as a
Blueprint. It introduces four new capabilities:

  • Analytics   — risk/return metrics (Sharpe, Sortino, max drawdown, win rate,
                  profit factor, expectancy) computed from the existing read-only
                  ledgers. No writes.
  • Journal     — a trade journal stored in a SEPARATE local database
                  (journal.db). This is the only place that writes, and it never
                  touches any trading ledger.
  • Alerts      — threshold rules stored in alerts.db, evaluated read-only
                  against current book metrics.
  • Order ticket— a SIMULATED, paper-only order entry. Orders are recorded as
                  rows in orders.db and are NEVER sent to a broker. There is no
                  code path here that places a real order or moves money.
  • Export      — CSV downloads + a printable performance report.

Design note on live execution: the submit handler deliberately stops at writing
a local "SIMULATED" row. A `# LIVE EXECUTION HOOK` marker shows exactly where a
real broker call would go if the operator ever chooses to wire one — but that is
intentionally left unimplemented.
"""

from config import PAPER_CAPITAL
import csv
import io
import math
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Blueprint, Response, abort, redirect, render_template,
                   request, session, url_for)

BASE_DIR         = Path(__file__).parent
PORTFOLIO_DB     = BASE_DIR / "portfolio.db"
INTRADAY_DB      = BASE_DIR / "intraday.db"
OPTIONS_DB       = BASE_DIR / "options.db"
CONDOR_DB        = BASE_DIR / "condor.db"
JOURNAL_DB       = BASE_DIR / "journal.db"     # writable, isolated
ALERTS_DB        = BASE_DIR / "alerts.db"      # writable, isolated
ORDERS_DB        = BASE_DIR / "orders.db"      # writable, isolated (simulated only)

STARTING_CAPITAL = PAPER_CAPITAL
TRADING_DAYS     = 252

bp = Blueprint("features", __name__)


# ── auth (mirror of dashboard.login_required, kept local to avoid import cycle) ─

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ── read-only ledger access ────────────────────────────────────────────────────

def _ro(path):
    if not path.exists():
        return None
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


# ── writable feature DBs (isolated, created on demand) ──────────────────────────

def _rw(path, schema):
    first = not path.exists()
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    if first:
        c.executescript(schema)
        c.commit()
    else:
        c.executescript(schema)   # CREATE TABLE IF NOT EXISTS — safe to re-run
        c.commit()
    return c


JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created   TEXT NOT NULL,
    book      TEXT,
    symbol    TEXT,
    side      TEXT,
    tag       TEXT,
    rating    INTEGER,
    title     TEXT,
    note      TEXT
);
"""

ALERTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    target         TEXT,
    op             TEXT NOT NULL,
    threshold      REAL NOT NULL,
    note           TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    last_triggered TEXT
);
"""

ORDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    order_type  TEXT NOT NULL,
    limit_price REAL,
    note        TEXT,
    mode        TEXT NOT NULL DEFAULT 'PAPER',
    status      TEXT NOT NULL DEFAULT 'SIMULATED'
);
"""


# ════════════════════════════════════════════════════════════════════════════
#  METRICS
# ════════════════════════════════════════════════════════════════════════════

def _series_metrics(daily_pnl):
    """Risk/return metrics from an ordered list of per-day net P&L values.
    Equity compounds off STARTING_CAPITAL. Returns a dict (all None-safe)."""
    out = {"sharpe": None, "sortino": None, "max_dd": 0.0, "max_dd_pct": 0.0,
           "best_day": None, "worst_day": None, "vol_pct": None,
           "equity": [], "drawdown": []}
    if not daily_pnl:
        return out

    equity, peak = STARTING_CAPITAL, STARTING_CAPITAL
    rets, eq_curve, dd_curve = [], [], []
    for pnl in daily_pnl:
        prev = equity
        equity += pnl
        rets.append(pnl / prev if prev else 0.0)
        eq_curve.append(round(equity, 2))
        peak = max(peak, equity)
        dd = equity - peak
        dd_curve.append(round(dd, 2))
        out["max_dd"] = min(out["max_dd"], dd)
        out["max_dd_pct"] = min(out["max_dd_pct"], (dd / peak * 100) if peak else 0.0)

    out["equity"], out["drawdown"] = eq_curve, dd_curve
    out["best_day"], out["worst_day"] = max(daily_pnl), min(daily_pnl)

    n = len(rets)
    mean = sum(rets) / n
    if n >= 2:
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        sd = math.sqrt(var)
        out["vol_pct"] = sd * math.sqrt(TRADING_DAYS) * 100
        if sd > 0:
            out["sharpe"] = (mean / sd) * math.sqrt(TRADING_DAYS)
        downs = [r for r in rets if r < 0]
        if downs:
            dsd = math.sqrt(sum(r * r for r in downs) / len(downs))
            if dsd > 0:
                out["sortino"] = (mean / dsd) * math.sqrt(TRADING_DAYS)
    return out


def _trade_metrics(net_pnls):
    """Win-rate / profit-factor / expectancy from a list of per-trade net P&L."""
    out = {"n": len(net_pnls), "win_rate": None, "profit_factor": None,
           "pf_label": "—", "avg_win": None, "avg_loss": None, "expectancy": None,
           "largest_win": None, "largest_loss": None, "payoff": None}
    if not net_pnls:
        return out
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    out["win_rate"] = len(wins) / len(net_pnls) * 100
    gp, gl = sum(wins), -sum(losses)
    if gl > 0:
        out["profit_factor"] = gp / gl
        out["pf_label"] = "%.2f" % out["profit_factor"]
    elif gp > 0:
        out["profit_factor"] = None
        out["pf_label"] = "∞"   # only winners, no losses
    out["avg_win"] = (gp / len(wins)) if wins else None
    out["avg_loss"] = (-gl / len(losses)) if losses else None
    out["expectancy"] = sum(net_pnls) / len(net_pnls)
    out["largest_win"] = max(net_pnls)
    out["largest_loss"] = min(net_pnls)
    if out["avg_win"] is not None and out["avg_loss"]:
        out["payoff"] = abs(out["avg_win"] / out["avg_loss"])
    return out


def intraday_strategies():
    c = _ro(INTRADAY_DB)
    if c is None:
        return []
    try:
        return [r["strategy"] for r in
                c.execute("SELECT strategy FROM account ORDER BY strategy")]
    finally:
        c.close()


def strategy_analytics(strategy):
    """Combined daily-series + per-trade metrics for one intraday strategy."""
    c = _ro(INTRADAY_DB)
    if c is None:
        return None
    try:
        daily = [r["net_pnl"] or 0.0 for r in c.execute(
            "SELECT net_pnl FROM days WHERE strategy=? ORDER BY trade_date",
            (strategy,))]
        dates = [r["trade_date"] for r in c.execute(
            "SELECT trade_date FROM days WHERE strategy=? ORDER BY trade_date",
            (strategy,))]
        trades = [r["net_pnl"] or 0.0 for r in c.execute(
            "SELECT net_pnl FROM trades WHERE strategy=?", (strategy,))]
        cash = c.execute("SELECT cash FROM account WHERE strategy=?",
                         (strategy,)).fetchone()
        equity = cash["cash"] if cash else STARTING_CAPITAL
    finally:
        c.close()

    sm = _series_metrics(daily)
    tm = _trade_metrics(trades)
    return {
        "strategy": strategy, "dates": dates, "equity": equity,
        "cum_net": sum(daily), "ret_pct": (equity - STARTING_CAPITAL) / STARTING_CAPITAL * 100,
        **{k: sm[k] for k in ("sharpe", "sortino", "max_dd", "max_dd_pct",
                              "best_day", "worst_day", "vol_pct")},
        "equity_curve": sm["equity"], "drawdown_curve": sm["drawdown"],
        "trade": tm,
    }


# ════════════════════════════════════════════════════════════════════════════
#  ANALYTICS PAGE
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/analytics")
@login_required
def analytics():
    strategies = intraday_strategies()
    if not strategies:
        return render_template("analytics.html", active="analytics",
                               error="No intraday data yet — run the paper bot first.",
                               strategies=[], selected=None, a=None, chart=None)
    selected = request.args.get("strategy")
    if selected not in strategies:
        selected = strategies[0]
    a = strategy_analytics(selected)
    chart = None
    if a and a["dates"]:
        chart = {"labels": a["dates"],
                 "equity": a["equity_curve"],
                 "drawdown": a["drawdown_curve"]}
    return render_template("analytics.html", active="analytics", error=None,
                           strategies=strategies, selected=selected, a=a, chart=chart)


# ════════════════════════════════════════════════════════════════════════════
#  TRADE JOURNAL  (writes to journal.db only)
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/journal")
@login_required
def journal():
    c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
    try:
        entries = [dict(r) for r in c.execute(
            "SELECT * FROM entries ORDER BY id DESC LIMIT 200")]
        n = len(entries)
        avg_rating = (sum(e["rating"] for e in entries if e["rating"]) /
                      max(1, sum(1 for e in entries if e["rating"]))) if entries else None
    finally:
        c.close()
    books = intraday_strategies() + ["lowvol", "options", "condor", "live", "other"]
    return render_template("journal.html", active="journal",
                           entries=entries, n=n, avg_rating=avg_rating, books=books)


@bp.route("/journal/add", methods=["POST"])
@login_required
def journal_add():
    f = request.form
    title = (f.get("title") or "").strip()
    note = (f.get("note") or "").strip()
    if not title and not note:
        return redirect(url_for("features.journal"))
    try:
        rating = int(f.get("rating") or 0) or None
    except ValueError:
        rating = None
    c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
    try:
        c.execute(
            "INSERT INTO entries (created, book, symbol, side, tag, rating, title, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),
             (f.get("book") or "").strip(), (f.get("symbol") or "").strip().upper(),
             (f.get("side") or "").strip(), (f.get("tag") or "").strip(),
             rating, title, note))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.journal"))


@bp.route("/journal/<int:entry_id>/delete", methods=["POST"])
@login_required
def journal_delete(entry_id):
    c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
    try:
        c.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.journal"))


# ════════════════════════════════════════════════════════════════════════════
#  ALERTS  (rules in alerts.db; evaluated read-only against current metrics)
# ════════════════════════════════════════════════════════════════════════════

ALERT_KINDS = {
    "PAPER_EQUITY":   "Paper book equity (₹)",
    "PAPER_REALISED": "Paper realised P&L (₹)",
    "INTRADAY_NET":   "Intraday strategy cumulative net (₹)",
    "INTRADAY_DD":    "Intraday strategy drawdown (₹, negative)",
    "OPTIONS_PNL":    "Options strangle total P&L (₹)",
    "CONDOR_PNL":     "Iron condor total P&L (₹)",
}


def _paper_equity_and_realised():
    c = _ro(PORTFOLIO_DB)
    if c is None:
        return None, None
    try:
        cash = c.execute("SELECT cash FROM account WHERE id=1").fetchone()
        cash = cash["cash"] if cash else STARTING_CAPITAL
        pos_cost = c.execute(
            "SELECT COALESCE(SUM(qty*avg_price),0) v FROM positions").fetchone()["v"]
        realised = c.execute(
            "SELECT COALESCE(SUM(realised_pnl),0) r FROM fills").fetchone()["r"]
        return cash + pos_cost, realised   # equity uses cost basis (no live fetch)
    finally:
        c.close()


def _options_pnl(db):
    c = _ro(db)
    if c is None:
        return None
    try:
        cash = c.execute("SELECT cash FROM account WHERE id=1").fetchone()
        realised = (cash["cash"] - STARTING_CAPITAL) if cash else 0.0
        cyc = c.execute("SELECT id FROM cycles WHERE status='open'").fetchone()
        unreal = 0.0
        if cyc:
            last = c.execute(
                "SELECT open_pnl FROM marks WHERE cycle_id=? ORDER BY mark_date DESC LIMIT 1",
                (cyc["id"],)).fetchone()
            unreal = last["open_pnl"] if last else 0.0
        return realised + unreal
    finally:
        c.close()


def _alert_value(rule):
    """Current metric value for a rule, or None if unavailable."""
    kind, target = rule["kind"], rule["target"]
    if kind == "PAPER_EQUITY":
        return _paper_equity_and_realised()[0]
    if kind == "PAPER_REALISED":
        return _paper_equity_and_realised()[1]
    if kind in ("INTRADAY_NET", "INTRADAY_DD"):
        a = strategy_analytics(target) if target else None
        if not a:
            return None
        return a["cum_net"] if kind == "INTRADAY_NET" else a["max_dd"]
    if kind == "OPTIONS_PNL":
        return _options_pnl(OPTIONS_DB)
    if kind == "CONDOR_PNL":
        return _options_pnl(CONDOR_DB)
    return None


def _triggered(value, op, threshold):
    if value is None:
        return None
    return value >= threshold if op == ">=" else value <= threshold


@bp.route("/alerts")
@login_required
def alerts():
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        rules = [dict(r) for r in c.execute("SELECT * FROM rules ORDER BY id DESC")]
    finally:
        c.close()

    evaluated, fired_ids = [], []
    for r in rules:
        val = _alert_value(r)
        fired = _triggered(val, r["op"], r["threshold"]) if r["active"] else None
        if fired:
            fired_ids.append(r["id"])
        evaluated.append({**r, "value": val, "fired": fired,
                          "kind_label": ALERT_KINDS.get(r["kind"], r["kind"])})

    if fired_ids:   # stamp last_triggered (the one write the alerts page makes)
        c = _rw(ALERTS_DB, ALERTS_SCHEMA)
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            c.executemany("UPDATE rules SET last_triggered=? WHERE id=?",
                          [(now, i) for i in fired_ids])
            c.commit()
        finally:
            c.close()

    n_fired = sum(1 for e in evaluated if e["fired"])
    return render_template("alerts.html", active="alerts", rules=evaluated,
                           kinds=ALERT_KINDS, strategies=intraday_strategies(),
                           n_fired=n_fired)


@bp.route("/alerts/add", methods=["POST"])
@login_required
def alerts_add():
    f = request.form
    kind = f.get("kind")
    op = f.get("op")
    if kind not in ALERT_KINDS or op not in (">=", "<="):
        return redirect(url_for("features.alerts"))
    try:
        threshold = float(f.get("threshold"))
    except (TypeError, ValueError):
        return redirect(url_for("features.alerts"))
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        c.execute(
            "INSERT INTO rules (created, kind, target, op, threshold, note, active) "
            "VALUES (?,?,?,?,?,?,1)",
            (datetime.now().strftime("%Y-%m-%d %H:%M"), kind,
             (f.get("target") or "").strip(), op, threshold,
             (f.get("note") or "").strip()))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.alerts"))


@bp.route("/alerts/<int:rule_id>/toggle", methods=["POST"])
@login_required
def alerts_toggle(rule_id):
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        c.execute("UPDATE rules SET active = 1 - active WHERE id=?", (rule_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.alerts"))


@bp.route("/alerts/<int:rule_id>/delete", methods=["POST"])
@login_required
def alerts_delete(rule_id):
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        c.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.alerts"))


# ════════════════════════════════════════════════════════════════════════════
#  ORDER TICKET  —  SIMULATED / PAPER ONLY.  Never contacts a broker.
# ════════════════════════════════════════════════════════════════════════════

@bp.route("/ticket")
@login_required
def ticket():
    c = _rw(ORDERS_DB, ORDERS_SCHEMA)
    try:
        orders = [dict(r) for r in c.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 100")]
    finally:
        c.close()
    symbols = []
    pc = _ro(PORTFOLIO_DB)
    if pc is not None:
        try:
            symbols = [r["symbol"] for r in
                       pc.execute("SELECT symbol FROM positions ORDER BY symbol")]
        finally:
            pc.close()
    return render_template("ticket.html", active="ticket", orders=orders, symbols=symbols)


@bp.route("/ticket/submit", methods=["POST"])
@login_required
def ticket_submit():
    f = request.form
    symbol = (f.get("symbol") or "").strip().upper()
    side = f.get("side")
    order_type = f.get("order_type") or "MARKET"
    try:
        qty = int(f.get("qty"))
    except (TypeError, ValueError):
        qty = 0
    if not symbol or side not in ("BUY", "SELL") or qty <= 0:
        return redirect(url_for("features.ticket"))
    try:
        limit_price = float(f.get("limit_price")) if f.get("limit_price") else None
    except ValueError:
        limit_price = None

    # ── This is the safety boundary. We persist a SIMULATED row and stop. ──
    c = _rw(ORDERS_DB, ORDERS_SCHEMA)
    try:
        c.execute(
            "INSERT INTO orders (created, symbol, side, qty, order_type, limit_price, note, mode, status) "
            "VALUES (?,?,?,?,?,?,?, 'PAPER', 'SIMULATED')",
            (datetime.now().strftime("%Y-%m-%d %H:%M"), symbol, side, qty,
             order_type, limit_price, (f.get("note") or "").strip()))
        c.commit()
    finally:
        c.close()
    # LIVE EXECUTION HOOK — intentionally NOT implemented.
    # A real integration would place the broker order here only after explicit,
    # per-order human confirmation and a hard live-trading enable flag.
    return redirect(url_for("features.ticket"))


@bp.route("/ticket/<int:order_id>/delete", methods=["POST"])
@login_required
def ticket_delete(order_id):
    c = _rw(ORDERS_DB, ORDERS_SCHEMA)
    try:
        c.execute("DELETE FROM orders WHERE id=?", (order_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.ticket"))


# ════════════════════════════════════════════════════════════════════════════
#  EXPORT  —  CSV downloads + printable report
# ════════════════════════════════════════════════════════════════════════════

def _csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/export/<kind>.csv")
@login_required
def export_csv(kind):
    if kind == "paper_positions":
        c = _ro(PORTFOLIO_DB)
        if c is None:
            abort(404)
        try:
            rows = [(r["symbol"], r["qty"], r["avg_price"], r["opened"]) for r in
                    c.execute("SELECT symbol, qty, avg_price, opened FROM positions ORDER BY symbol")]
        finally:
            c.close()
        return _csv_response("paper_positions.csv",
                             ["symbol", "qty", "avg_price", "opened"], rows)

    if kind == "paper_fills":
        c = _ro(PORTFOLIO_DB)
        if c is None:
            abort(404)
        try:
            rows = [(r["run_date"], r["symbol"], r["side"], r["qty"], r["price"],
                     r["cost"], r["realised_pnl"]) for r in c.execute(
                "SELECT run_date, symbol, side, qty, price, cost, realised_pnl "
                "FROM fills ORDER BY id")]
        finally:
            c.close()
        return _csv_response("paper_fills.csv",
                             ["run_date", "symbol", "side", "qty", "price", "cost", "realised_pnl"], rows)

    if kind == "intraday_trades":
        c = _ro(INTRADAY_DB)
        if c is None:
            abort(404)
        strat = request.args.get("strategy")
        try:
            if strat:
                cur = c.execute(
                    "SELECT trade_date, strategy, symbol, side, entry_time, entry_px, "
                    "exit_time, exit_px, qty, gross_pnl, costs, net_pnl, exit_reason "
                    "FROM trades WHERE strategy=? ORDER BY id", (strat,))
            else:
                cur = c.execute(
                    "SELECT trade_date, strategy, symbol, side, entry_time, entry_px, "
                    "exit_time, exit_px, qty, gross_pnl, costs, net_pnl, exit_reason "
                    "FROM trades ORDER BY id")
            rows = [tuple(r) for r in cur]
        finally:
            c.close()
        return _csv_response("intraday_trades.csv",
                             ["trade_date", "strategy", "symbol", "side", "entry_time",
                              "entry_px", "exit_time", "exit_px", "qty", "gross_pnl",
                              "costs", "net_pnl", "exit_reason"], rows)

    if kind == "journal":
        c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
        try:
            rows = [(r["created"], r["book"], r["symbol"], r["side"], r["tag"],
                     r["rating"], r["title"], r["note"]) for r in
                    c.execute("SELECT * FROM entries ORDER BY id")]
        finally:
            c.close()
        return _csv_response("journal.csv",
                             ["created", "book", "symbol", "side", "tag", "rating", "title", "note"], rows)

    abort(404)


@bp.route("/report")
@login_required
def report():
    strategies = intraday_strategies()
    books = [strategy_analytics(s) for s in strategies]
    books = [b for b in books if b]
    equity, realised = _paper_equity_and_realised()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render_template("report.html", books=books, generated=generated,
                           paper_equity=equity, paper_realised=realised,
                           starting=STARTING_CAPITAL)
