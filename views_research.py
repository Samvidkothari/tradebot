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
                        login_required, paper_db, live_price, warm_prices,
                        BOOK_STATUS, status_for, sparkline_svg, ro_db)
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
        _enrich_book(r)
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


# ── Portfolio Overview hero (combined paper books, dated series) ───────────────

def _ro(db_name):
    return ro_db(BASE_DIR / db_name)


def _cumulative(rows, key):
    cum, out = 0.0, []
    for r in rows:
        cum += (r[key] or 0.0)
        out.append(round(cum, 2))
    return out


def _lowvol_spark():
    conn = paper_db()
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl),0) v FROM fills WHERE side='SELL' "
        "GROUP BY run_date ORDER BY run_date").fetchall()
    conn.close()
    return _cumulative(rows, "v")


def _intraday_spark(strategy):
    c = _ro("intraday.db")
    if c is None:
        return []
    rows = c.execute("SELECT net_pnl FROM days WHERE strategy=? ORDER BY trade_date",
                     (strategy,)).fetchall()
    c.close()
    return _cumulative(rows, "net_pnl")


def _option_spark(db_name):
    c = _ro(db_name)
    if c is None:
        return []
    rows = c.execute("SELECT open_pnl FROM marks ORDER BY mark_date").fetchall()
    c.close()
    return [r["open_pnl"] or 0.0 for r in rows]


def _combined_curve():
    """Combined cumulative realised P&L by date across the dated books
    (low-vol SELL realised + every intraday strategy's daily net)."""
    inc = {}
    conn = paper_db()
    if conn is not None:
        for r in conn.execute(
            "SELECT run_date d, COALESCE(SUM(realised_pnl),0) v FROM fills "
            "WHERE side='SELL' GROUP BY run_date"):
            inc[r["d"]] = inc.get(r["d"], 0.0) + (r["v"] or 0.0)
        conn.close()
    c = _ro("intraday.db")
    if c is not None:
        for r in c.execute(
            "SELECT trade_date d, COALESCE(SUM(net_pnl),0) v FROM days GROUP BY trade_date"):
            inc[r["d"]] = inc.get(r["d"], 0.0) + (r["v"] or 0.0)
        c.close()
    # Settled options/condor cycles count as realised P&L on their close date.
    for db in ("options.db", "condor.db"):
        c = _ro(db)
        if c is None:
            continue
        for r in c.execute(
            "SELECT close_date d, COALESCE(SUM(settle_pnl),0) v FROM cycles "
            "WHERE status='closed' AND close_date IS NOT NULL GROUP BY close_date"):
            if r["d"]:
                inc[r["d"]] = inc.get(r["d"], 0.0) + (r["v"] or 0.0)
        c.close()
    if not inc:
        return None
    dates = sorted(inc)
    cum, vals = 0.0, []
    for d in dates:
        cum += inc[d]
        vals.append(round(cum, 2))
    return {"labels": dates, "values": vals}


def _enrich_book(r):
    """Attach a spark series, status key + pill, and spark SVG to a P&L row,
    choosing the right series source from the book's name. Shared by the
    overview hero and the consolidated P&L page so they stay consistent."""
    name = r["book"]
    low = name.lower()
    if low.startswith("intraday "):
        r["spark"] = _intraday_spark(name.replace("Intraday ", ""))
    elif "strangle" in low:
        r["spark"] = _option_spark("options.db")
    elif "condor" in low:
        r["spark"] = _option_spark("condor.db")
    elif "low-vol" in low or "lowvol" in low:
        r["spark"] = _lowvol_spark()
    else:
        r["spark"] = []
    if "total" not in r:
        r["total"] = r["realised"] + r["unrealised"]
    r["status_key"] = status_for(None, name)
    r["pill"] = BOOK_STATUS.get(r["status_key"], BOOK_STATUS["paper"])
    r["spark_svg"] = sparkline_svg(r["spark"])
    return r


def _book_rows():
    """Every paper book with realised/unrealised, a UI status, and a spark series."""
    rows = []
    lv = _pnl_lowvol()
    if lv:
        rows.append(dict(lv))
    for db, label in (("options.db", "Options strangle"), ("condor.db", "Options condor")):
        ob = _pnl_option_book(db, label, "mark-to-model")
        if ob:
            rows.append(dict(ob))
    rows += [dict(r) for r in _pnl_intraday()]
    return [_enrich_book(r) for r in rows]


def overview_hero():
    rows = _book_rows()
    curve = _combined_curve()
    realised = sum(r["realised"] for r in rows)
    unrealised = sum(r["unrealised"] for r in rows)
    best = max(rows, key=lambda r: r["total"]) if rows else None
    worst = min(rows, key=lambda r: r["total"]) if rows else None
    return {
        "rows": rows, "curve": curve,
        "realised": realised, "unrealised": unrealised, "total": realised + unrealised,
        "n_books": len(rows),
        "n_active": sum(1 for r in rows if r["status"] == "active"),
        "best": best, "worst": worst,
    }


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
            "pill": BOOK_STATUS.get(status_for(s.get("kind"), s.get("label") or s.get("name")),
                                    BOOK_STATUS["paper"]),
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
    return render_template("monitor.html", active="monitor",
                           ov=_overview_data(), hero=overview_hero())


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


# ── Command surface (Quiet Terminal redesign) ─────────────────────────────────
# Two redesigned, data-wired pages served alongside the existing dashboard. They
# READ the same research JSONs and book P&L the other pages do — no new analytics,
# no order path. Mounted at /command and /command/risk; nothing existing changes.

_RISK_LIMITS = BASE_DIR / "risk_limits.json"


def _isnan(x):
    try:
        return x != x
    except Exception:
        return False


def _limits():
    try:
        return json.loads(_RISK_LIMITS.read_text())
    except Exception:
        return {}


def _verdict(pct):
    """Map a 0..100 position-in-band to a verdict class."""
    if pct >= 90:
        return "crit", "is-crit"
    if pct >= 70:
        return "act", "is-act"
    if pct >= 45:
        return "watch", "is-watch"
    return "normal", "is-normal"


def _track(value, limit, *, normal_frac=0.5):
    """Marker % of |value| against |limit| (a budget-used reading), clamped."""
    lim = abs(limit) or 1.0
    pct = max(0.0, min(100.0, abs(value) / lim * 100.0))
    verdict, klass = _verdict(pct)
    return {"mark": round(pct, 1), "band_w": round(normal_frac * 100, 1),
            "verdict": verdict, "klass": klass}


def _regime_label(reg):
    if not reg:
        return {"label": "Unknown", "reason": "Regime engine hasn't run yet."}
    trend = (reg.get("trend") or "").replace("_", " ").title()
    vol = (reg.get("volatility") or "").replace("_", " ").replace("Volatility", "Vol").title()
    char = (reg.get("character") or "").replace("_", "-")
    parts = [p for p in (trend, vol, char) if p]
    return {"label": " · ".join(parts) or "Unknown", "reason": reg.get("reason", "")}


def _command_risk_rows(rk, re):
    """Build explainable risk rows (value · range · verdict · action) from the
    live risk_report (rk) and risk_engine (re) JSON, plus configured limits."""
    lim = _limits()
    rows = []
    lv = ((rk or {}).get("strategies") or {}).get("lowvol") or {}
    dd = lv.get("drawdown") or {}
    var = lv.get("var") or {}
    tail = lv.get("tail") or {}
    checks = (re or {}).get("checks") or {}

    def row(name, val_str, t, action, note=""):
        rows.append({"name": name, "val": val_str, **t, "action": action, "note": note})

    # Drawdown vs the -20% stop
    cur = dd.get("current_drawdown")
    if cur is not None:
        t = _track(cur, lim.get("max_drawdown_limit", -0.20))
        row("Current Drawdown", f"{cur*100:.1f}%", t,
            ("Inside the stop — no action." if t["verdict"] == "normal"
             else "Past half the drawdown budget — size down new entries."),
            f"stop {lim.get('max_drawdown_limit',-0.2)*100:.0f}%")
    mdd = dd.get("max_drawdown")
    if mdd is not None:
        t = _track(mdd, lim.get("max_drawdown_limit", -0.20)); t["klass"] = "is-normal"; t["verdict"] = "info"
        row("Max Drawdown · hist", f"{mdd*100:.1f}%", t, "Worst peak-to-trough on record — context for the stop.", "since inception")

    # Daily loss (engine check)
    dl = checks.get("daily_loss") or {}
    if "value" in dl:
        t = _track(dl["value"], dl.get("limit", -0.03));
        row("Daily Loss", f"{dl['value']*100:.1f}%", t,
            "Within the daily limit." if dl.get("status") == "OK" else "Daily loss limit breached — halt new entries.",
            f"limit {dl.get('limit',-0.03)*100:.0f}%")

    # Portfolio heat (annualised vol) vs 12% target
    av = tail.get("ann_vol")
    if av is not None:
        pct = min(100.0, av / 0.18 * 100.0)
        verdict, klass = _verdict(pct)
        if av <= 0.12:
            verdict, klass = "normal", "is-normal"
        elif av <= 0.135:
            verdict, klass = "watch", "is-watch"
        row("Portfolio Heat", f"{av*100:.1f}%", {"mark": round(pct, 1), "band_w": 67, "verdict": verdict, "klass": klass},
            "Cool — room to add risk." if verdict == "normal" else "Above the 12% target — trim or hedge before adding.",
            "target 12%")

    # VaR / CVaR
    v95 = var.get("hist_95")
    if v95 is not None:
        pct = min(100.0, v95 / 0.02 * 100.0); verdict, klass = _verdict(pct)
        row("VaR · 1d 95%", f"−{v95*100:.2f}%", {"mark": round(pct, 1), "band_w": 60, "verdict": verdict, "klass": klass},
            "95% of days should lose less than this.", "alert 2%")
    cv = var.get("cvar_95")
    if cv is not None:
        pct = min(100.0, cv / 0.025 * 100.0); verdict, klass = _verdict(pct)
        row("CVaR · 95%", f"−{cv*100:.2f}%", {"mark": round(pct, 1), "band_w": 60, "verdict": verdict, "klass": klass},
            "Average loss on the worst 5% of days.", "tail risk")

    # Correlation (engine check)
    co = checks.get("correlation") or {}
    if "value" in co and not _isnan(co["value"]):
        t = _track(co["value"], co.get("limit", 0.50))
        row("Avg Correlation", f"{co['value']:.2f}", t,
            "Names move independently enough." if co.get("status") == "OK" else "Crowded — diversify across factors.",
            f"limit {co.get('limit',0.5):.2f}")

    # Sector exposure (may be NaN when no equity marks)
    se = checks.get("sector_exposure") or {}
    if "value" in se:
        if _isnan(se["value"]):
            row("Sector Exposure", "n/a", {"mark": 0, "band_w": 88, "verdict": "info", "klass": "is-normal"},
                "No sector marks in this run — refresh equity prices.", f"limit {se.get('limit',0.35)*100:.0f}%")
        else:
            t = _track(se["value"], se.get("limit", 0.35))
            row("Sector Exposure · max", f"{se['value']*100:.0f}%", t,
                "Within the sector cap." if se.get("status") == "OK" else "Concentrated — trim the heaviest sector.",
                f"limit {se.get('limit',0.35)*100:.0f}%")
    return rows


def _banner_ctx():
    """Shared status-banner context (read-only flags)."""
    re, _ = _research_json("risk_engine.json", "risk_engine.py")
    as_of = (re or {}).get("as_of", "—")
    risk_ok = (re or {}).get("status") == "OK"
    return {"as_of": as_of, "risk_ok": risk_ok,
            "risk_text": "Normal" if risk_ok else (re or {}).get("status", "—")}


def command():
    hero = overview_hero()
    ov = _overview_data()
    re, _ = _research_json("risk_engine.json", "risk_engine.py")
    rk, _ = _research_json("risk.json", "risk_report.py")
    cap = STARTING_CAPITAL * max(1, hero["n_books"])
    net = cap + hero["total"]
    risk_rows = _command_risk_rows(rk, re)
    # dashboard shows the 4 headline risk rows
    risk_mini = [r for r in risk_rows if r["name"] in
                 ("Current Drawdown", "Portfolio Heat", "Avg Correlation", "VaR · 1d 95%")]
    opt_active = sum(1 for r in hero["rows"]
                     if r["status"] == "active" and "Options" in r["book"])
    ret_count = sum(1 for r in hero["rows"] if r["status"] == "retired")
    return render_template("command_dashboard.html", active="command",
                           hero=hero, cap=cap, net=net,
                           pnl_pct=(hero["total"] / cap * 100 if cap else 0),
                           regime=_regime_label(ov.get("regime")),
                           risk_mini=risk_mini, risk_status=(re or {}).get("status", "—"),
                           opt_active=opt_active, ret_count=ret_count,
                           banner=_banner_ctx())


def command_risk():
    re, _ = _research_json("risk_engine.json", "risk_engine.py")
    rk, _ = _research_json("risk.json", "risk_report.py")
    rows = _command_risk_rows(rk, re)
    emergency = bool((re or {}).get("emergency"))
    n_watch = sum(1 for r in rows if r["verdict"] in ("watch", "act", "crit"))
    return render_template("command_risk.html", active="command_risk",
                           rows=rows, status=(re or {}).get("status", "—"),
                           emergency=emergency, reason=(re or {}).get("reason", ""),
                           n_watch=n_watch, banner=_banner_ctx())


def register(app):
    """Attach research routes with their ORIGINAL endpoint names (so url_for in
    templates is unchanged). Each view is login-gated."""
    rules = [
        ("/command", "command", command),
        ("/command/risk", "command_risk", command_risk),
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
