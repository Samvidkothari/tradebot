"""api/books.py — book rows + per-book simulated ledgers (read-only JSON)."""

import sqlite3

from flask import jsonify, abort

from web_common import BASE_DIR, INTRADAY_DB, STARTING_CAPITAL, login_required, \
    paper_db, ro_db
import views_research as vr

from . import bp


def _clean(row):
    """Book row → JSON-safe (drop server-rendered SVG, keep raw spark data)."""
    r = dict(row)
    r.pop("spark_svg", None)
    return r


@bp.get("/overview")
@login_required
def api_overview():
    rows = [_clean(r) for r in vr._book_rows()]
    active = [r for r in rows if r["status"] == "active"]
    banner = vr._banner_ctx()
    ts, _ = vr._research_json("tearsheets.json", "tearsheet.py")
    return jsonify({
        "capital_per_book": STARTING_CAPITAL,
        "books": rows,
        "active": {
            "n": len(active),
            "capital": STARTING_CAPITAL * max(1, len(active)),
            "realised": sum(r["realised"] for r in active),
            "unrealised": sum(r["unrealised"] for r in active),
            "total": sum(r["total"] for r in active),
        },
        "regime": (ts or {}).get("regime"),
        "vol_event": banner["vol"],
        "as_of": banner["as_of"],
        "paper": True,
    })


@bp.get("/books")
@login_required
def api_books():
    return jsonify({"books": [_clean(r) for r in vr._book_rows()]})


LEDGER_KEYS = ("lowvol", "strangle", "condor", "orb", "vwap")


@bp.get("/books/<key>/ledger")
@login_required
def api_ledger(key):
    key = key.lower()
    if key not in LEDGER_KEYS:
        abort(404)

    if key == "lowvol":
        conn = paper_db()
        if conn is None:
            return jsonify({"error": "portfolio.db not found"}), 404
        try:
            fills = [dict(r) for r in conn.execute(
                "SELECT run_date, symbol, side, qty, price, cost, realised_pnl "
                "FROM fills ORDER BY id DESC LIMIT 200")]
            positions = [dict(r) for r in conn.execute(
                "SELECT symbol, qty, avg_price, opened FROM positions "
                "ORDER BY symbol")]
        finally:
            conn.close()
        return jsonify({"book": "lowvol", "fills": fills, "positions": positions})

    if key in ("strangle", "condor"):
        db = "options.db" if key == "strangle" else "condor.db"
        conn = ro_db(BASE_DIR / db)
        if conn is None:
            return jsonify({"error": f"{db} not found"}), 404
        try:
            cycles = [dict(r) for r in conn.execute(
                "SELECT * FROM cycles ORDER BY id DESC LIMIT 24")]
            marks = [dict(r) for r in conn.execute(
                "SELECT * FROM marks ORDER BY mark_date DESC LIMIT 120")]
        finally:
            conn.close()
        return jsonify({"book": key, "cycles": cycles, "marks": marks})

    # orb / vwap — retired, frozen evidence
    conn = ro_db(INTRADAY_DB)
    if conn is None:
        return jsonify({"error": "intraday.db not found"}), 404
    try:
        strat = key.upper()
        trades = [dict(r) for r in conn.execute(
            "SELECT trade_date, symbol, side, entry_px, exit_px, qty, "
            "gross_pnl, costs, net_pnl, exit_reason FROM trades "
            "WHERE strategy = ? ORDER BY id DESC LIMIT 200", (strat,))]
        days = [dict(r) for r in conn.execute(
            "SELECT * FROM days WHERE strategy = ? ORDER BY trade_date",
            (strat,))]
    finally:
        conn.close()
    return jsonify({"book": key, "retired": True, "trades": trades, "days": days})
