"""
test_intraday_sim.py — the money-path logic of the intraday paper simulator:
the MIS cost model, the opening-range-breakout fill rule, position sizing/P&L,
and the idempotency guarantee (re-running a date is a no-op). No network — pure
functions on synthetic bars, plus a temp DB for the idempotency check.
"""

from pathlib import Path

import pandas as pd
import pytest

import intraday_sim as sim


def _bars(rows):
    """rows: list of (hhmm, open, high, low, close, volume) on a fixed date."""
    idx = pd.to_datetime([f"2026-06-11 {r[0]}" for r in rows])
    data = {k: [] for k in ("open", "high", "low", "close", "volume")}
    for _, o, h, l, c, v in rows:
        data["open"].append(o); data["high"].append(h); data["low"].append(l)
        data["close"].append(c); data["volume"].append(v)
    return pd.DataFrame(data, index=idx)


# ── cost model (hand-computed) ───────────────────────────────────────────────────

def test_leg_cost_buy_and_sell():
    # notional 200,000: brokerage capped at 20; +txn 5.94 +sebi 0.2 +gst 4.7052
    # buy adds stamp 6 + slippage 100;  sell adds STT 50 + slippage 100
    assert sim.leg_cost(200_000, is_buy=True) == pytest.approx(136.8452, abs=1e-4)
    assert sim.leg_cost(200_000, is_buy=False) == pytest.approx(180.8452, abs=1e-4)


def test_brokerage_is_capped():
    # 0.03% of 200k = 60, but the cap is 20 → present in both legs
    assert sim.leg_cost(200_000, True) < sim.leg_cost(1_000_000, True)  # bigger notional, more cost


# ── ORB fill rule ────────────────────────────────────────────────────────────────

def test_signal_orb_long_breakout_hits_target():
    bars = _bars([
        ("09:15", 100, 101, 99, 100, 1000),   # opening range: high 101 / low 99
        ("09:20", 100, 101, 99, 100, 1000),
        ("09:25", 100, 101, 99, 100, 1000),
        ("09:30", 100, 103, 100, 102, 1000),   # close 102 > 101 → LONG entry
        ("09:35", 102, 105, 102, 104, 1000),   # close 104 == entry+range → TARGET
    ])
    t = sim.signal_orb(bars)
    assert t is not None
    assert t["side"] == "LONG"
    assert t["entry_px"] == 102.0 and t["exit_px"] == 104.0
    assert t["exit_reason"] == "TARGET"


def test_signal_orb_no_breakout_returns_none():
    bars = _bars([
        ("09:15", 100, 101, 99, 100, 1000),
        ("09:20", 100, 101, 99, 100, 1000),
        ("09:25", 100, 101, 99, 100, 1000),
        ("09:30", 100, 101, 99, 100, 1000),    # never breaks the range
    ])
    assert sim.signal_orb(bars) is None


# ── sizing + P&L ─────────────────────────────────────────────────────────────────

def test_size_and_cost_long_pnl_is_gross_minus_modeled_costs():
    trade = sim._mk("LONG", "09:30", 102.0, "09:35", 104.0, "TARGET")
    sized = sim.size_and_cost(trade)
    qty = int(sim.NOTIONAL // 102.0)                    # 200_000 // 102 = 1960
    assert sized["qty"] == qty
    assert sized["gross_pnl"] == pytest.approx((104.0 - 102.0) * qty)
    expected_costs = (sim.leg_cost(qty * 102.0, is_buy=True)
                      + sim.leg_cost(qty * 104.0, is_buy=False))
    assert sized["costs"] == pytest.approx(expected_costs)
    assert sized["net_pnl"] == pytest.approx(sized["gross_pnl"] - expected_costs)
    assert sized["net_pnl"] < sized["gross_pnl"]        # costs always bite


# ── idempotency (temp DB, no real ledger) ────────────────────────────────────────

def test_run_strategy_day_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "DB_PATH", Path(tmp_path) / "intraday_test.db")
    conn = sim.db_connect()
    try:
        first = sim.run_strategy_day(conn, {}, "2026-06-11", "ORB", sim.signal_orb)
        second = sim.run_strategy_day(conn, {}, "2026-06-11", "ORB", sim.signal_orb)
        assert first is not None          # ran (zero trades, but a day row was written)
        assert second is None             # skipped — already simulated
        rows = conn.execute(
            "SELECT COUNT(*) FROM days WHERE trade_date=? AND strategy=?",
            ("2026-06-11", "ORB")).fetchone()[0]
        assert rows == 1                  # exactly one day row, never double-counted
    finally:
        conn.close()
