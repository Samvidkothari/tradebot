"""
features/core.py — shared blueprint, DB helpers, schemas, and metrics for the
feature pages.

The route handlers live in the sibling modules (analytics, journal, alerts,
ticket, exports). They ALL register on the single Blueprint named "features"
defined here, so every `url_for('features.…')` in the templates keeps resolving
exactly as before the split. READ-ONLY except the isolated feature DBs; no code
path here places a real order.
"""

import csv
import io
import math
from datetime import datetime

from flask import Blueprint, Response

from web_common import (BASE_DIR, STARTING_CAPITAL, login_required, ro_db, rw_db,
                        BOOK_STATUS, status_for, sparkline_svg)

# ── paths / constants ──────────────────────────────────────────────────────────
PORTFOLIO_DB = BASE_DIR / "portfolio.db"
INTRADAY_DB  = BASE_DIR / "intraday.db"
OPTIONS_DB   = BASE_DIR / "options.db"
CONDOR_DB    = BASE_DIR / "condor.db"
JOURNAL_DB   = BASE_DIR / "journal.db"     # writable, isolated
ALERTS_DB    = BASE_DIR / "alerts.db"      # writable, isolated
ORDERS_DB    = BASE_DIR / "orders.db"      # writable, isolated (simulated only)
TRADING_DAYS = 252

# The one blueprint every feature route attaches to.
bp = Blueprint("features", __name__)


# ── DB access (canonical helpers live in web_common; thin aliases here) ─────────
def _ro(path):
    return ro_db(path)


def _rw(path, schema):
    return rw_db(path, schema)


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
#  ALERT METRIC RESOLUTION  (read-only against current book state)
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


# ── export helper ───────────────────────────────────────────────────────────────
def _csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
