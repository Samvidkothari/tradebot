"""
test_llm_analyst.py — the LLM paper book: untrusted-output validation + paper P&L.

The whole point of this book is that the model's JSON is treated as hostile data.
These tests pin that: unknown symbols dropped, weights clamped, gross scaled,
malformed output → no trade, and a full cycle only ever writes the isolated paper
ledger (no order path). Offline throughout (deterministic fallback decider).
"""
import json

import pytest

import llm_analyst as LA

UNI = {"INFY", "TCS", "RELIANCE", "HDFCBANK", "ITC", "SBIN", "WIPRO", "LT"}


def test_parse_valid_decision():
    txt = ('{"decisions":[{"action":"BUY","symbol":"INFY","weight":0.1},'
           '{"action":"SELL","symbol":"TCS"}],"confidence":0.7,"rationale":"x"}')
    p = LA.parse_decision(txt, UNI)
    assert p["accepted"] and p["target"]["INFY"] == 0.1 and p["target"]["TCS"] == 0.0
    assert p["confidence"] == 0.7


def test_parse_rejects_unknown_symbol_and_clamps_weight():
    txt = ('{"decisions":[{"action":"BUY","symbol":"rm -rf /","weight":99},'
           '{"action":"BUY","symbol":"INFY","weight":0.9}],"confidence":5}')
    p = LA.parse_decision(txt, UNI)
    assert any("not-in-universe" in d for d in p["dropped"])      # injection dropped
    assert p["target"]["INFY"] == LA.MAX_NAME_WEIGHT              # 0.9 clamped
    assert 0.0 <= p["confidence"] <= 1.0                          # 5 clamped


def test_parse_scales_gross_exposure():
    ds = [{"action": "BUY", "symbol": s, "weight": 0.15} for s in UNI]   # 8×0.15=1.2
    p = LA.parse_decision(json.dumps({"decisions": ds}), UNI)
    gross = sum(w for w in p["target"].values() if w > 0)
    assert gross <= LA.MAX_GROSS + 1e-6                           # scaled down


def test_parse_malformed_holds():
    p = LA.parse_decision("the market looks bullish — buy everything", UNI)
    assert p["accepted"] is False and p["target"] is None


def test_fallback_decider_is_valid():
    msgs = LA.build_prompt({"factor_leaderboard": [{"symbol": "INFY"}, {"symbol": "TCS"}]})
    p = LA.parse_decision(LA._fallback(msgs), UNI)
    assert p["accepted"] and p["target"]


def test_full_cycle_is_paper_only(tmp_path, monkeypatch):
    monkeypatch.setattr(LA, "DB_PATH", tmp_path / "llm.db")
    px = {"INFY": 1500.0, "TCS": 3900.0, "RELIANCE": 2900.0}
    monkeypatch.setattr(LA, "prices", lambda: px)
    monkeypatch.setattr(LA, "universe", lambda: set(px))
    monkeypatch.setattr(LA, "_load_json", lambda name:
                        ({"top": [{"symbol": "INFY", "composite": .7},
                                  {"symbol": "TCS", "composite": .6}], "as_of": "2026-06-30"}
                         if "multifactor" in name else {"regime": {"tags": ["bear"]}}))
    conn = LA.db_connect()
    s = LA.run_cycle(conn)
    assert s["accepted"]                                          # fallback decided
    eq = LA.equity(conn, px)
    assert abs(eq - LA.CAPITAL) < LA.CAPITAL * 0.02               # only costs move day 1
    assert LA.positions(conn)                                     # holdings created
    assert conn.execute("SELECT COUNT(*) FROM marks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    conn.close()


def test_no_order_path_in_source():
    src = open("llm_analyst.py").read()
    for bad in ("place_order", "modify_order", "cancel_order", "KiteConnect", "kite."):
        assert bad not in src, f"unexpected order-path token: {bad}"
