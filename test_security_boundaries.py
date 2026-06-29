"""
test_security_boundaries.py — assert the app's safety boundaries explicitly.

Covers: path-traversal / input-validation guards on the file-backed routes, and
the hard rule that the order ticket only ever records a SIMULATED row (never a
live order). The ticket DB is redirected to a tmp path so the test writes nothing
to the real ledger and runs anywhere.
"""

import os
import sqlite3

import dashboard
import features.ticket as ticket_mod

# Offline-safe: never hit yfinance during these tests.
dashboard.fetch_live = lambda *a, **k: None

app = dashboard.app
app.testing = True
HERE = os.path.dirname(os.path.abspath(__file__))


def _client():
    c = app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
    return c


# ── path-traversal / input validation ───────────────────────────────────────────

def test_backtests_rejects_non_md_name():
    assert _client().get("/backtests/secret.txt").status_code == 404


def test_backtests_rejects_missing_report():
    assert _client().get("/backtests/definitely-not-here.md").status_code == 404


def test_backtests_serves_a_real_report_if_present():
    res_dir = os.path.join(HERE, "results")
    reports = [f for f in os.listdir(res_dir) if f.endswith(".md")] if os.path.isdir(res_dir) else []
    if not reports:
        return  # nothing to serve; guard tests above still cover the boundary
    assert _client().get(f"/backtests/{reports[0]}").status_code == 200


def test_candles_rejects_unknown_symbol():
    assert _client().get("/api/candles/NOT_A_REAL_SYMBOL").status_code == 404


def test_candles_serves_known_symbol_if_present():
    data_dir = os.path.join(HERE, "data")
    syms = [f[:-4] for f in os.listdir(data_dir) if f.endswith(".csv")] if os.path.isdir(data_dir) else []
    if not syms:
        return
    assert _client().get(f"/api/candles/{syms[0]}").status_code == 200


# ── order ticket never leaves "SIMULATED" ────────────────────────────────────────

def test_ticket_submit_only_records_simulated_row(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setattr(ticket_mod, "ORDERS_DB", db)   # redirect write off the real ledger

    c = _client()
    rv = c.post("/ticket/submit", data={
        "symbol": "reliance", "side": "BUY", "qty": "10",
        "order_type": "MARKET", "note": "boundary test"})
    assert rv.status_code == 302

    rows = sqlite3.connect(str(db)).execute(
        "SELECT status, mode, side, qty FROM orders ORDER BY id DESC LIMIT 1").fetchall()
    assert rows, "ticket submit recorded no row"
    status, mode, side, qty = rows[0]
    assert status == "SIMULATED" and mode == "PAPER"   # the hard safety boundary
    assert side == "BUY" and qty == 10


def _order_count(db):
    """Row count, treating a never-created DB/table as zero."""
    if not db.exists():
        return 0
    try:
        return sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def test_ticket_submit_rejects_invalid_order(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setattr(ticket_mod, "ORDERS_DB", db)
    c = _client()
    rv = c.post("/ticket/submit", data={"symbol": "", "side": "BUY", "qty": "-5",
                                        "order_type": "MARKET"})
    assert rv.status_code == 302
    assert _order_count(db) == 0   # invalid input must not create a row
