"""
test_options_condor.py — the money-path math of the two options paper books.

Like the web-layer metrics, these functions render a plausible number even when
wrong (a mispriced option still produces a P&L row), so nothing downstream would
catch a silent error — this is where the pricing, the statutory cost model, the
expiry calendar, and the iron-condor's defined-risk invariant get pinned.

Strictly READ-ONLY and offline: pure functions on synthetic inputs, plus one
temp-DB structural check for the condor (the real options.db / condor.db are
never opened — DB_PATH is redirected to a tmp file).
"""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

import options_sim as opt
import condor_sim as con


# ── Black–Scholes (shared by both books) ─────────────────────────────────────

def test_bs_put_call_parity():
    # C - P must equal S - K·e^{-rT} for the same strike/expiry (no-arb identity).
    S, K, T, vol, r = 20000.0, 20000.0, 0.1, 0.15, 0.065
    c = opt.bs_price(S, K, T, vol, r, "call")
    p = opt.bs_price(S, K, T, vol, r, "put")
    assert (c - p) == pytest.approx(S - K * math.exp(-r * T), abs=0.5)


def test_bs_intrinsic_floor_at_or_after_expiry():
    # T <= 0 or vol <= 0 collapses to intrinsic value, never negative.
    assert opt.bs_price(21000, 20000, 0.0, 0.15, 0.065, "call") == 1000.0
    assert opt.bs_price(19000, 20000, 0.0, 0.15, 0.065, "put") == 1000.0
    assert opt.bs_price(20000, 20000, 0.0, 0.15, 0.065, "call") == 0.0
    assert opt.bs_price(21000, 20000, -1.0, 0.15, 0.065, "call") == 1000.0  # vol<=0


def test_bs_call_monotonic_in_spot():
    f = lambda s: opt.bs_price(s, 20000, 0.1, 0.15, 0.065, "call")
    assert f(19000) < f(20000) < f(21000)
    assert 0.0 < f(20000) < 20000  # a call is worth something, but less than spot


# ── Statutory cost model (per leg, per side) ─────────────────────────────────

def test_statutory_uncapped_regime():
    # premium 100 pts → turnover 7,500; brokerage 0.0003·7500 = 2.25 (< 20 cap),
    # txn 0.0003503·7500, GST 18% on (brokerage + txn).
    turnover = 100 * opt.LOT_SIZE
    brok = min(opt.BROKERAGE_CAP, 0.0003 * turnover)
    txn = opt.EXCH_TXN * turnover
    expected = brok + txn + opt.GST * (brok + txn)
    assert opt.statutory(100) == pytest.approx(expected, abs=1e-6)
    assert brok < opt.BROKERAGE_CAP  # this regime is below the cap


def test_statutory_brokerage_cap_binds():
    # premium 2,000 pts → turnover 150,000; 0.0003·150000 = 45 > 20 → brokerage
    # pins at the ₹20 cap (only txn + GST keep scaling past it).
    turnover = 2000 * opt.LOT_SIZE
    assert 0.0003 * turnover > opt.BROKERAGE_CAP
    txn = opt.EXCH_TXN * turnover
    expected = opt.BROKERAGE_CAP + txn + opt.GST * (opt.BROKERAGE_CAP + txn)
    assert opt.statutory(2000) == pytest.approx(expected, abs=1e-6)


def test_statutory_positive_and_increasing():
    vals = [opt.statutory(p) for p in (10, 100, 500, 2000)]
    assert all(v > 0 for v in vals)
    assert vals == sorted(vals)


# ── Expiry calendar ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("y,m", [(2026, 1), (2026, 6), (2026, 7), (2026, 12)])
def test_last_thursday_is_a_thursday_in_month(y, m):
    d = opt.last_thursday(y, m)
    assert d.weekday() == 3          # Thursday
    assert d.month == m and d.year == y
    import calendar
    assert calendar.monthrange(y, m)[1] - d.day < 7   # genuinely the *last* one


def test_next_expiry_respects_min_dte():
    today = date(2026, 6, 15)        # June expiry (25th) is only 10d out → must roll
    e = opt.next_expiry(today)
    assert e.weekday() == 3
    assert (e - today).days >= opt.OPEN_MIN_DTE


def test_t_years_business_day_basis():
    # Mon 2026-06-01 → Mon 2026-06-08 is exactly 5 business days (end-exclusive).
    assert opt.t_years(date(2026, 6, 1), date(2026, 6, 8)) == pytest.approx(5 / 252.0)
    # expiry already reached / passed never goes negative.
    assert opt.t_years(date(2026, 6, 8), date(2026, 6, 8)) == 0.0
    assert opt.t_years(date(2026, 6, 8), date(2026, 6, 1)) == 0.0


# ── Realized vol ─────────────────────────────────────────────────────────────

def test_realized_vol_constant_series_is_zero():
    s = pd.Series([100.0] * 30)
    assert opt.realized_vol(s) == 0.0


def test_realized_vol_needs_two_returns():
    assert opt.realized_vol(pd.Series([100.0])) is None


def test_realized_vol_is_annualized_std_of_log_returns():
    rng = np.random.default_rng(0)
    closes = pd.Series(1000 * np.cumprod(1 + rng.normal(0, 0.01, 60)))
    rets = np.log(closes / closes.shift(1)).dropna().iloc[-opt.RV_WINDOW:]
    assert opt.realized_vol(closes) == pytest.approx(rets.std(ddof=1) * math.sqrt(252))


# ── Iron condor: the defined-risk invariant (the digest's "max loss capped") ──

def test_unwind_cost_is_net_of_shorts_minus_wings():
    assert con._unwind_cost_pts(10, 8, 3, 2) == pytest.approx((10 + 8) - (3 + 2))


def test_condor_open_caps_loss_at_wing_width_minus_credit(tmp_path, monkeypatch):
    monkeypatch.setattr(con, "DB_PATH", tmp_path / "condor_test.db")
    conn = con.db_connect()
    con.open_condor(conn, date(2026, 6, 15), spot=24000.0, vol=0.15)
    cyc = con.open_cycle(conn)
    conn.close()

    sc, sp, lc, lp = cyc["sc_strike"], cyc["sp_strike"], cyc["lc_strike"], cyc["lp_strike"]
    # Bodies straddle spot; wings sit strictly outside the bodies.
    assert sp < 24000 < sc
    assert lc > sc and lp < sp

    width = max(lc - sc, sp - lp) * con.LOT_SIZE
    # Defined risk: a net credit is collected and the loss is hard-capped.
    assert cyc["premium_net"] > 0
    assert 0 < cyc["max_loss"] < width
    # The invariant itself: max_loss == wing width − net credit kept.
    assert cyc["max_loss"] + cyc["premium_net"] == pytest.approx(width, abs=2.0)
