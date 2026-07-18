"""
test_notify_telegram.py — the fail-soft notifier contract + batch-script wiring.

All tests stub the HTTP layer (`_http_post`) so NOTHING reaches real Telegram;
we assert on the captured payloads instead. Covers the new synchronous
`send_sync` / `notify` path used by paper_trader / options_sim / condor_sim.
"""

import notify_telegram
from notify_telegram import TelegramNotifier


# ── send_sync / notify contract ───────────────────────────────────────────────

def test_send_sync_noop_when_disabled(monkeypatch):
    """No token/chat -> disabled -> send_sync must not even attempt a POST."""
    called = []
    monkeypatch.setattr(notify_telegram, "_http_post",
                        lambda *a, **k: called.append(a))
    TelegramNotifier(None, None).send_sync("hello")
    assert called == []


def test_send_sync_delivers_when_enabled(monkeypatch):
    """Enabled -> send_sync does exactly one blocking POST with our text."""
    sent = []

    def fake_post(url, payload, timeout=5):
        sent.append(payload)
        return {"ok": True}

    monkeypatch.setattr(notify_telegram, "_http_post", fake_post)
    TelegramNotifier("123:ABC", "999").send_sync("filled 🎯")
    assert len(sent) == 1
    assert sent[0]["chat_id"] == "999"
    assert sent[0]["text"] == "filled 🎯"


def test_send_sync_never_raises_on_http_error(monkeypatch):
    """Rule 2: a network/HTTP failure is swallowed, not propagated."""
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(notify_telegram, "_http_post", boom)
    # Must simply return; any raise fails the test.
    TelegramNotifier("123:ABC", "999").send_sync("will fail quietly")


def test_module_notify_uses_singleton(monkeypatch):
    """notify() routes through get_notifier(); disabled singleton -> no POST."""
    called = []
    monkeypatch.setattr(notify_telegram, "_http_post",
                        lambda *a, **k: called.append(a))
    monkeypatch.setattr(notify_telegram, "_notifier",
                        TelegramNotifier(None, None))
    notify_telegram.notify("nothing should send")
    assert called == []


def test_send_sync_truncates_to_max_len(monkeypatch):
    """Over-long text is clipped to Telegram's headroom cap before sending."""
    sent = []
    monkeypatch.setattr(notify_telegram, "_http_post",
                        lambda url, payload, timeout=5: sent.append(payload))
    TelegramNotifier("123:ABC", "999").send_sync("x" * 10_000)
    assert len(sent[0]["text"]) == notify_telegram.MAX_LEN


# ── batch-script hooks fire through notify() (captured, not sent) ─────────────

def test_condor_close_notifies(monkeypatch, tmp_path):
    """condor_sim._close pushes a settlement alert with the P&L."""
    import condor_sim
    msgs = []
    monkeypatch.setattr(condor_sim.notify_telegram, "notify", msgs.append)
    monkeypatch.setattr(condor_sim, "DB_PATH", tmp_path / "condor.db")
    conn = condor_sim.db_connect()
    conn.execute(
        "INSERT INTO cycles (id, open_date, expiry, spot_open, sc_strike, "
        "sp_strike, lc_strike, lp_strike, premium_net, max_loss, status) "
        "VALUES (1,'2026-07-01','2026-07-31',24000,25000,23000,25500,22500,"
        "5000,10000,'open')")
    cyc = condor_sim.open_cycle(conn)
    condor_sim._close(conn, cyc, __import__("datetime").date(2026, 7, 31),
                      "EXPIRY", 4200.0)
    conn.close()
    assert len(msgs) == 1
    assert "Condor EXPIRY" in msgs[0] and "4,200" in msgs[0]


def test_options_close_notifies(monkeypatch, tmp_path):
    """options_sim._close pushes a settlement alert (loss icon on negative P&L)."""
    import options_sim
    msgs = []
    monkeypatch.setattr(options_sim.notify_telegram, "notify", msgs.append)
    monkeypatch.setattr(options_sim, "DB_PATH", tmp_path / "options.db")
    conn = options_sim.db_connect()
    conn.execute(
        "INSERT INTO cycles (id, open_date, expiry, spot_open, call_strike, "
        "put_strike, premium_net, status) "
        "VALUES (1,'2026-07-01','2026-07-31',24000,25000,23000,5000,'open')")
    cyc = options_sim.open_cycle(conn)
    options_sim._close(conn, cyc, __import__("datetime").date(2026, 7, 31),
                       "STOP", -1500.0)
    conn.close()
    assert len(msgs) == 1
    assert "Strangle STOP" in msgs[0] and "🔻" in msgs[0]


def test_paper_trader_summary_notifies(monkeypatch, tmp_path):
    """paper_trader._notify_run summarises fills + equity in one message."""
    import paper_trader
    msgs = []
    monkeypatch.setattr(paper_trader.notify_telegram, "notify", msgs.append)
    monkeypatch.setattr(paper_trader, "DB_PATH", tmp_path / "portfolio.db")
    conn = paper_trader.db_connect()
    # Two BUYs today, one SELL with realised P&L.
    for side, pnl in (("BUY", None), ("BUY", None), ("SELL", 250.0)):
        conn.execute(
            "INSERT INTO fills (run_date, symbol, side, qty, price, cost, "
            "cash_delta, realised_pnl) VALUES ('2026-07-17','X',?,1,100,0,0,?)",
            (side, pnl))
    paper_trader._notify_run(conn, "2026-07-17", {},
                             "🔁 Monthly rebalance to 15 lowest-vol names")
    conn.close()
    assert len(msgs) == 1
    assert "2 BUY / 1 SELL" in msgs[0]
    assert "rebalance" in msgs[0].lower()
    assert "Realised today" in msgs[0]
