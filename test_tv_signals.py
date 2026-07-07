"""
test_tv_signals.py — TradingView webhook → paper signals book.

Pins the safety contract: the shared secret is required, the payload is untrusted
(unknown symbols / bad actions / oversized weights rejected or clamped), a valid
alert only moves the isolated paper ledger, and there is no order path. Offline.
"""
import flask
import pytest

import tv_signals as TV

UNI = {"RELIANCE", "INFY", "TCS"}
PX = {"RELIANCE": 2900.0, "INFY": 1500.0, "TCS": 3900.0}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(TV, "DB_PATH", tmp_path / "tv.db")
    monkeypatch.setattr(TV, "universe", lambda: set(UNI))
    monkeypatch.setattr(TV, "_last_prices", lambda: dict(PX))
    monkeypatch.setenv("TV_WEBHOOK_SECRET", "s3cr3t")
    return tmp_path


def test_disabled_without_secret(iso, monkeypatch):
    monkeypatch.delenv("TV_WEBHOOK_SECRET", raising=False)
    r = TV.handle({"secret": "x", "action": "BUY", "symbol": "INFY"})
    assert r["ok"] is False and "disabled" in r["note"]


def test_bad_secret_rejected(iso):
    r = TV.handle({"secret": "nope", "action": "BUY", "symbol": "INFY"})
    assert r["ok"] is False and "secret" in r["note"]


def test_unknown_symbol_rejected(iso):
    r = TV.handle({"secret": "s3cr3t", "action": "BUY", "symbol": "NSE:HACKER"})
    assert r["ok"] is False and "universe" in r["note"]


def test_bad_action_rejected(iso):
    r = TV.handle({"secret": "s3cr3t", "action": "rm -rf /", "symbol": "INFY"})
    assert r["ok"] is False


def test_valid_buy_then_close(iso):
    r = TV.handle({"secret": "s3cr3t", "action": "BUY", "symbol": "NSE:INFY",
                   "weight": 0.1, "price": 1500})
    assert r["ok"] and r["n_holdings"] == 1
    assert abs(r["equity"] - TV.CAPITAL) < TV.CAPITAL * 0.01      # only cost moves it
    r2 = TV.handle({"secret": "s3cr3t", "action": "CLOSE", "symbol": "INFY"})
    assert r2["ok"] and r2["n_holdings"] == 0


def test_weight_is_clamped(iso):
    TV.handle({"secret": "s3cr3t", "action": "BUY", "symbol": "INFY", "weight": 99, "price": 1500})
    conn = TV.db_connect()
    val = TV.positions(conn)["INFY"]["qty"] * 1500
    conn.close()
    assert val <= TV.CAPITAL * TV.MAX_NAME_WEIGHT * 1.05         # 99 → clamped to 15%


def test_route_returns_json(iso):
    app = flask.Flask(__name__)
    TV.register(app)
    c = app.test_client()
    ok = c.post("/api/tv/webhook", json={"secret": "s3cr3t", "action": "BUY",
                                         "symbol": "INFY", "price": 1500})
    assert ok.status_code == 200 and ok.get_json()["ok"]
    bad = c.post("/api/tv/webhook", json={"secret": "wrong", "action": "BUY", "symbol": "INFY"})
    assert bad.status_code == 400


def test_no_order_path_in_source():
    src = open("tv_signals.py").read()
    for bad in ("place_order", "modify_order", "cancel_order", "KiteConnect", "kite."):
        assert bad not in src, f"unexpected order-path token: {bad}"
