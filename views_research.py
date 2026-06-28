"""
views_research.py — research / summary dashboard pages.

Extracted from dashboard.py (which grew past 800 lines). Holds the Home summary,
consolidated P&L, the six research tabs (tear sheets, factors, portfolio, risk,
attribution, data quality) and the backtest-report viewer.

`register(app)` attaches each handler with its ORIGINAL endpoint name, so every
existing `url_for(...)` in the templates keeps working unchanged. READ-ONLY; no
order-placement code.
"""

import json

from flask import abort, render_template

from digest import build_digest
from web_common import (BASE_DIR, RESULTS_DIR, INTRADAY_DB, STARTING_CAPITAL,
                        login_required, paper_db, live_price, warm_prices)
import sqlite3


def home():
    return render_template("home.html", active="home", digest=build_digest())


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


def data_quality_view():
    data, error = _research_json("data_quality.json", "data_quality.py")
    return render_template("data_quality.html", active="data_quality",
                           data=data, error=error)


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


def risk_view():
    data, error = _research_json("risk.json", "risk_report.py")
    return render_template("risk.html", active="risk", data=data, error=error)


def portfolio_analysis():
    data, error = _research_json("portfolio.json", "portfolio_analyzer.py")
    return render_template("portfolio_analysis.html", active="portfolio_analysis",
                           data=data, error=error)


def factors_view():
    data, error = _research_json("factors.json", "factor_report.py")
    return render_template("factors.html", active="factors", data=data, error=error)


def feature_store_view():
    data, error = _research_json("feature_store.json", "feature_store.py")
    return render_template("feature_store.html", active="feature_store",
                           data=data, error=error)


# ── Backtest reports (results/*.md) ───────────────────────────────────────────

def backtests():
    reports = sorted(fp.name for fp in RESULTS_DIR.glob("*.md")) \
        if RESULTS_DIR.exists() else []
    return render_template("backtests.html", active="backtests",
                           reports=reports, current=None, content=None)


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


def register(app):
    """Attach research routes with their ORIGINAL endpoint names (so url_for in
    templates is unchanged). Each view is login-gated."""
    rules = [
        ("/home", "home", home),
        ("/pnl", "pnl", pnl),
        ("/tearsheet", "tearsheet", tearsheet),
        ("/factors", "factors_view", factors_view),
        ("/feature-store", "feature_store_view", feature_store_view),
        ("/portfolio-analysis", "portfolio_analysis", portfolio_analysis),
        ("/risk", "risk_view", risk_view),
        ("/attribution", "attribution_view", attribution_view),
        ("/data-quality", "data_quality_view", data_quality_view),
        ("/backtests", "backtests", backtests),
        ("/backtests/<name>", "backtest_view", backtest_view),
    ]
    for path, endpoint, fn in rules:
        app.add_url_rule(path, endpoint, login_required(fn))
