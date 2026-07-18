"""
test_heartbeat.py — the daily status ping composes correctly and is fail-soft.

No network, no real Telegram: we point the DB paths at temp SQLite files and
assert on the composed message text.
"""

import sqlite3

import heartbeat


def _mk_options_db(path, table_cols, row):
    """Minimal account+cycles+marks DB matching the option books' shape."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL)")
    conn.execute("INSERT INTO account VALUES (1, 1000000)")
    conn.execute(f"CREATE TABLE cycles ({table_cols})")
    conn.execute("CREATE TABLE marks (cycle_id INT, mark_date TEXT, open_pnl REAL)")
    conn.execute(*row)
    conn.execute("INSERT INTO marks VALUES (1, '2026-07-18', 3823.79)")
    conn.commit()
    conn.close()


def test_missing_dbs_are_graceful(monkeypatch, tmp_path):
    """No DB files yet -> every book reports 'not started', nothing raises."""
    for attr in ("PORTFOLIO_DB", "CONDOR_DB", "OPTIONS_DB"):
        monkeypatch.setattr(heartbeat, attr, tmp_path / f"{attr}.missing")
    msg = heartbeat.build_message("2026-07-18")
    assert msg.count("(not started yet)") == 3
    assert "daily status — 2026-07-18" in msg


def test_condor_open_cycle_rendered(monkeypatch, tmp_path):
    """An open condor shows OPEN, expiry, body strikes and mark P&L."""
    db = tmp_path / "condor.db"
    _mk_options_db(
        db,
        "id INT, status TEXT, expiry TEXT, sc_strike REAL, sp_strike REAL, "
        "settle_pnl REAL",
        ("INSERT INTO cycles VALUES (1,'open','2026-07-30',25000,23100,NULL)",))
    monkeypatch.setattr(heartbeat, "CONDOR_DB", db)
    block = heartbeat.condor_block()
    assert "🦅 Iron condor" in block
    assert "OPEN exp 2026-07-30" in block
    assert "23100P/25000C" in block
    assert "mark P&L +₹3,823.79" in block


def test_lowvol_prefers_marked_equity(monkeypatch, tmp_path):
    """When the governor has a marked equity, the heartbeat uses it (not cost)."""
    db = tmp_path / "portfolio.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE account (id INTEGER PRIMARY KEY, cash REAL);
        CREATE TABLE positions (symbol TEXT, qty INT, avg_price REAL);
        CREATE TABLE fills (side TEXT, realised_pnl REAL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO account VALUES (1, 50000);
        INSERT INTO positions VALUES ('X', 10, 100);
        INSERT INTO fills VALUES ('SELL', 500);
        INSERT INTO meta VALUES ('governor_last', '{"date":"2026-07-18","equity":1234567.0}');
    """)
    conn.commit(); conn.close()
    monkeypatch.setattr(heartbeat, "PORTFOLIO_DB", db)
    block = heartbeat.lowvol_block()
    assert "marked" in block
    assert "₹1,234,567.00" in block
    assert "realized +₹500.00" in block


def test_build_message_is_failsoft(monkeypatch):
    """A block that raises is caught and annotated, not propagated."""
    monkeypatch.setattr(heartbeat, "condor_block",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(heartbeat, "lowvol_block", lambda: "ok-lowvol")
    monkeypatch.setattr(heartbeat, "strangle_block", lambda: "ok-strangle")
    msg = heartbeat.build_message("2026-07-18")
    assert "read failed (boom)" in msg
    assert "ok-lowvol" in msg and "ok-strangle" in msg


def test_dry_run_sends_nothing(monkeypatch, capsys):
    """--dry-run prints the message and must NOT call notify()."""
    sent = []
    monkeypatch.setattr(heartbeat.notify_telegram, "notify", sent.append)
    monkeypatch.setattr(heartbeat.sys, "argv", ["heartbeat.py", "--dry-run"])
    heartbeat.main()
    assert sent == []
    assert "daily status" in capsys.readouterr().out
