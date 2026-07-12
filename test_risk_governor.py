"""test_risk_governor.py — the automated protection layer must be trustworthy."""

import json
import sqlite3

import risk_governor as rg

LIMITS = {"daily_loss_limit": -0.03, "max_drawdown_limit": -0.20,
          "auto_liquidate": False}


def _db(tmp_path, cash=1_000_000, positions=()):
    conn = sqlite3.connect(tmp_path / "p.db")
    conn.executescript("""
        CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL NOT NULL);
        CREATE TABLE positions (symbol TEXT PRIMARY KEY, qty INTEGER,
                                avg_price REAL, opened TEXT);
    """)
    conn.execute("INSERT INTO account VALUES (1, ?)", (cash,))
    for sym, qty, avg in positions:
        conn.execute("INSERT INTO positions VALUES (?,?,?,'2026-01-01')",
                     (sym, qty, avg))
    conn.commit()
    return conn


def test_equity_and_peak_tracking(tmp_path):
    conn = _db(tmp_path, cash=100_000, positions=[("A", 100, 500.0)])
    s1 = rg.mark(conn, {"A": 500.0}, LIMITS, today="2026-01-02")
    assert s1["equity"] == 150_000 and s1["peak"] == 150_000 and s1["ok"]
    s2 = rg.mark(conn, {"A": 600.0}, LIMITS, today="2026-01-03")   # up → new peak
    assert s2["peak"] == 160_000
    s3 = rg.mark(conn, {"A": 550.0}, LIMITS, today="2026-01-04")   # small dip: fine
    assert s3["peak"] == 160_000 and not s3["killed"] and s3["drawdown"] < 0


def test_kill_switch_trips_blocks_and_resets(tmp_path):
    conn = _db(tmp_path, cash=0, positions=[("A", 100, 1000.0)])
    rg.mark(conn, {"A": 1000.0}, LIMITS, today="2026-01-02")       # peak 100k
    s = rg.mark(conn, {"A": 790.0}, LIMITS, today="2026-01-03")    # -21% from peak
    assert s["killed"] and not s["ok"]
    ok, why = rg.allow_rebalance(s)
    assert not ok and "kill switch" in why
    # killed state persists on later, recovered marks
    s2 = rg.mark(conn, {"A": 990.0}, LIMITS, today="2026-01-04")
    assert s2["killed"]
    # human reset clears it and restarts the peak
    assert rg.reset(conn) is True
    s3 = rg.mark(conn, {"A": 990.0}, LIMITS, today="2026-01-05")
    assert not s3["killed"] and s3["ok"] and s3["peak"] == 99_000


def test_daily_loss_brake_soft_stops_without_killing(tmp_path):
    conn = _db(tmp_path, cash=0, positions=[("A", 100, 1000.0)])
    rg.mark(conn, {"A": 1000.0}, LIMITS, today="2026-01-02")
    s = rg.mark(conn, {"A": 950.0}, LIMITS, today="2026-01-03")    # -5% day, -5% DD
    assert s["daily_breach"] and not s["killed"]
    ok, why = rg.allow_rebalance(s)
    assert not ok and "daily-loss" in why
    # next day, flat → brake released automatically
    s2 = rg.mark(conn, {"A": 950.0}, LIMITS, today="2026-01-04")
    assert rg.allow_rebalance(s2)[0]


def test_state_persisted_for_ui(tmp_path):
    conn = _db(tmp_path)
    rg.mark(conn, {}, LIMITS, today="2026-01-02")
    st = rg.status(conn)
    assert st["ok"] and json.dumps(st)                              # JSON-safe


def test_missing_book_degrades(tmp_path):
    conn = sqlite3.connect(tmp_path / "empty.db")
    s = rg.mark(conn, {}, LIMITS)
    assert s["ok"] is None and not s["killed"]
