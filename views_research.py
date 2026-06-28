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


def multifactor_view():
    data, error = _research_json("multifactor.json", "multifactor.py")
    return render_template("multifactor.html", active="multifactor",
                           data=data, error=error)


def optimizer_view():
    data, error = _research_json("optimizer.json", "portfolio_optimizer.py")
    return render_template("optimizer.html", active="optimizer",
                           data=data, error=error)


def risk_engine_view():
    data, error = _research_json("risk_engine.json", "risk_engine.py")
    return render_template("risk_engine.html", active="risk_engine",
                           data=data, error=error)


def market_intel_view():
    data, error = _research_json("market_intel.json", "market_intel.py")
    return render_template("market_intel.html", active="market_intel",
                           data=data, error=error)


def automation_view():
    data, error = _research_json("pipeline_run.json", "research_pipeline.py")
    return render_template("automation.html", active="automation",
                           data=data, error=error)


def research_assistant_view():
    data, error = _research_json("research_assistant.json", "research_assistant.py")
    groups = []
    if data:
        sev_order = {"warn": 0, "watch": 1, "info": 2, "good": 3}
        area_order = ["Daily performance", "Alpha decay", "Overfitting",
                      "Factor performance", "Improvements", "Technical debt"]
        by_area = {}
        for f in data["findings"]:
            by_area.setdefault(f["area"], []).append(f)
        for area in area_order + [a for a in by_area if a not in area_order]:
            if area in by_area:
                groups.append((area, sorted(by_area[area],
                                            key=lambda x: sev_order.get(x["severity"], 9))))
    return render_template("research_assistant.html", active="research_assistant",
                           data=data, groups=groups, error=error)


# ── Overview: single monitoring page assembled from the research JSONs ─────────

def _overview_data():
    """Assemble the eight monitoring panels from results/*.json (read-only). Each
    panel degrades to None if its source hasn't been produced yet — the template
    shows a 'run X' hint rather than erroring. No new analytics: this READS what
    the pipeline already wrote."""
    ts, _ = _research_json("tearsheets.json", "tearsheet.py")
    fa, _ = _research_json("factors.json", "factor_report.py")
    at, _ = _research_json("attribution.json", "attribution_report.py")
    re, _ = _research_json("risk_engine.json", "risk_engine.py")
    hist, _ = _research_json("pipeline_history.json", "research_pipeline.py")
    last, _ = _research_json("pipeline_run.json", "research_pipeline.py")

    strategies = (ts or {}).get("strategies", {})

    # 1. Portfolio Performance — the live low-vol book's headline metrics.
    perf = None
    lv = strategies.get("lowvol")
    if lv and lv.get("full"):
        f = lv["full"]
        perf = {"label": lv.get("label", "Low-Volatility"),
                "cagr": f.get("cagr"), "total_return": f.get("total_return"),
                "max_drawdown": f.get("max_drawdown"), "sharpe": f.get("sharpe"),
                "alpha": f.get("alpha")}

    # 5. Strategy Comparison — every strategy, side by side, with regime fit.
    comparison = []
    for s in strategies.values():
        f = s.get("full") or {}
        rc = s.get("regime_compat") or {}
        comparison.append({
            "label": s.get("label", s.get("name")), "kind": s.get("kind"),
            "cagr": f.get("cagr"), "max_drawdown": f.get("max_drawdown"),
            "sharpe": f.get("sharpe"),
            "fit": (None if s.get("kind") == "options"
                    else "in" if rc.get("compatible") else "out")})

    # 2. Factor Exposure — each factor's strongest name + the ranker weights.
    factors = None
    if fa and fa.get("factors"):
        w = fa.get("weights", {})
        factors = [{"name": k, "weight": w.get(k),
                    "top_symbol": (v.get("top") or [{}])[0].get("symbol"),
                    "top_score": (v.get("top") or [{}])[0].get("score")}
                   for k, v in fa["factors"].items()]

    # 3. Sector Allocation — the low-vol book's current sector weights.
    sectors = None
    if at and at.get("strategies"):
        strat = at["strategies"].get("lowvol") or next(iter(at["strategies"].values()))
        bysec = (strat.get("holdings") or {}).get("by_sector") or {}
        total = sum(bysec.values()) or 1.0
        sectors = sorted(((k, v / total) for k, v in bysec.items()),
                         key=lambda kv: kv[1], reverse=True)

    return {
        "performance": perf,                    # 1
        "factors": factors,                     # 2
        "sectors": sectors,                     # 3
        "risk": re,                             # 4
        "comparison": comparison,               # 5
        "history": list(reversed(hist or []))[:8],  # 6 (most recent first)
        "last_run": last,                       # 6
        "regime": (ts or {}).get("regime"),     # 7
        "digest": build_digest(),               # 8
        "generated": (ts or {}).get("generated"),
    }


def monitor():
    return render_template("monitor.html", active="monitor", ov=_overview_data())


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
        ("/monitor", "monitor", monitor),
        ("/home", "home", home),
        ("/pnl", "pnl", pnl),
        ("/tearsheet", "tearsheet", tearsheet),
        ("/factors", "factors_view", factors_view),
        ("/feature-store", "feature_store_view", feature_store_view),
        ("/multi-factor", "multifactor_view", multifactor_view),
        ("/optimizer", "optimizer_view", optimizer_view),
        ("/risk-engine", "risk_engine_view", risk_engine_view),
        ("/market-intel", "market_intel_view", market_intel_view),
        ("/automation", "automation_view", automation_view),
        ("/research-assistant", "research_assistant_view", research_assistant_view),
        ("/portfolio-analysis", "portfolio_analysis", portfolio_analysis),
        ("/risk", "risk_view", risk_view),
        ("/attribution", "attribution_view", attribution_view),
        ("/data-quality", "data_quality_view", data_quality_view),
        ("/backtests", "backtests", backtests),
        ("/backtests/<name>", "backtest_view", backtest_view),
    ]
    for path, endpoint, fn in rules:
        app.add_url_rule(path, endpoint, login_required(fn))
