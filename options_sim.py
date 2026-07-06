"""
options_sim.py — Forward VRP short-strangle PAPER simulator (NIFTY).

Implements exactly strategies/SPEC_options.md (pre-registered, commit 98e4efb),
which rests on strategies/THESIS_options.md.

FORWARD, model-priced, paper-only. Each run, for the most recent NIFTY day:
  • if flat, open a monthly short strangle (sell a 4%-OTM call + 4%-OTM put),
    pricing each leg with Black–Scholes using 20-day realized vol as an IV proxy,
    and collecting premium net of a deliberately HARSH 10%/leg spread haircut;
  • if a position is open, mark it to model, flag a vol event on a >=4% NIFTY day,
    close early if the loss hits 2x the premium (paying the exit spread), and
    settle at intrinsic on the last-Thursday expiry.

THIS PLACES NO REAL ORDERS. Every fill is a row in options.db. It is paper-only,
and per SPEC/THESIS it must NEVER be wired to fully-autonomous live trading.

Usage:
  python options_sim.py            # advance the sim to the latest NIFTY day
"""

from config import PAPER_CAPITAL
import calendar
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import math
import numpy as np
import pandas as pd
import yfinance as yf

# ── Pre-registered parameters (SPEC_options.md — do not tune to results) ───────
OTM_PCT     = 0.04        # strikes 4% OTM
STRIKE_STEP = 50          # round strikes to nearest 50
LOT_SIZE    = 75          # NIFTY lot
CAPITAL     = PAPER_CAPITAL   # paper book (covers strangle margin)
RV_WINDOW   = 20          # trading days for realized vol
RISK_FREE   = 0.065
STOP_MULT   = 2.0         # close if loss >= 2x premium collected
SPREAD_PCT  = 0.10        # HARSH bid-ask haircut, per leg, per transaction
OPEN_MIN_DTE = 21         # v2 (SPEC §v2): open only on a monthly expiry >= 21
                          # calendar days out — no near-worthless stubs
VOL_EVENT   = 0.04        # |NIFTY daily move| that counts as a volatility event

# Light statutory costs on premium turnover (rupees) — secondary to the spread
BROKERAGE_CAP = 20.0
STT_RATE      = 0.000625  # 0.0625% on sell-side option premium
EXCH_TXN      = 0.0003503 # ~0.03503% of premium (NSE options)
GST           = 0.18
DB_PATH       = Path(__file__).parent / "options.db"
# ─────────────────────────────────────────────────────────────────────────────


# ── Black–Scholes (European, index option; prices in index points) ────────────

def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot, strike, T, vol, r, kind):
    """Black–Scholes price in index points. kind = 'call' | 'put'."""
    if T <= 0 or vol <= 0:
        return max(0.0, spot - strike) if kind == "call" else max(0.0, strike - spot)
    sq = vol * math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + vol * vol / 2) * T) / sq
    d2 = d1 - sq
    if kind == "call":
        return spot * _ncdf(d1) - strike * math.exp(-r * T) * _ncdf(d2)
    return strike * math.exp(-r * T) * _ncdf(-d2) - spot * _ncdf(-d1)


def statutory(premium_points):
    """Rupee statutory cost on one option leg's premium turnover (one side)."""
    turnover  = premium_points * LOT_SIZE
    brokerage = min(BROKERAGE_CAP, 0.0003 * turnover)
    txn       = EXCH_TXN * turnover
    gst       = GST * (brokerage + txn)
    return brokerage + txn + gst


# ── Expiry calendar ───────────────────────────────────────────────────────────

def last_thursday(year, month):
    last = calendar.monthrange(year, month)[1]
    d = date(year, month, last)
    return d - timedelta(days=(d.weekday() - 3) % 7)   # Thursday = weekday 3


def next_expiry(today):
    """Nearest monthly expiry at least OPEN_MIN_DTE calendar days out (v2)."""
    e = last_thursday(today.year, today.month)
    if (e - today).days < OPEN_MIN_DTE:
        y = today.year + (1 if today.month == 12 else 0)
        m = today.month % 12 + 1
        e = last_thursday(y, m)
    return e


def t_years(today, expiry):
    """Time to expiry in years, by NSE trading (business) days / 252."""
    bd = np.busday_count(today.isoformat(), expiry.isoformat())
    return max(int(bd), 0) / 252.0


# ── Database ──────────────────────────────────────────────────────────────────

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            open_date TEXT, expiry TEXT, spot_open REAL,
            call_strike REAL, put_strike REAL,
            premium_gross REAL, entry_spread REAL, entry_stat REAL, premium_net REAL,
            status TEXT, close_date TEXT, close_reason TEXT, settle_pnl REAL,
            vol_event INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS marks (
            cycle_id INTEGER, mark_date TEXT, spot REAL, daily_move REAL,
            call_val REAL, put_val REAL, open_pnl REAL,
            PRIMARY KEY (cycle_id, mark_date));
        CREATE TABLE IF NOT EXISTS precommit (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, committed TEXT NOT NULL);
    """)
    # Pre-committed stress-test criteria — INSERT OR IGNORE: written ONCE,
    # never updated by code, so the judgment rules cannot drift after the fact.
    for k, v in (
        ("vol_event_threshold", f"{VOL_EVENT}"),
        ("event_definition", "|NIFTY daily move| >= 4% while a cycle is short"),
        ("stop_rule", f"close early if open loss >= {STOP_MULT}x net premium"),
        ("win_criteria", "WIN if, through a cycle containing a vol event, the "
                         "stop was not breached and the cycle settles net "
                         "positive after all spreads/statutory costs"),
        ("loss_criteria", "LOSS if the stop fires during the event or the "
                          "event cycle settles net negative; a stop breach on "
                          "a gap beyond 2x premium is the thesis's known "
                          "unlimited-tail failure mode"),
        ("verdict_gate", "INCONCLUSIVE until >= 1 vol event occurs while "
                         "short; quiet months prove nothing"),
    ):
        conn.execute("INSERT OR IGNORE INTO precommit (key, value, committed) "
                     "VALUES (?,?,date('now'))", (k, v))
    if conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone() is None:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)", (CAPITAL,))
        conn.commit()
    return conn


def get_cash(conn):
    return conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()["cash"]


def open_cycle(conn):
    return conn.execute("SELECT * FROM cycles WHERE status = 'open'").fetchone()


# ── Data ──────────────────────────────────────────────────────────────────────

def nifty_daily():
    raw = yf.Ticker("^NSEI").history(period="1y", interval="1d", auto_adjust=True)
    if raw.empty:
        return None
    s = raw["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s.sort_index()


def realized_vol(closes):
    rets = np.log(closes / closes.shift(1)).dropna().iloc[-RV_WINDOW:]
    if len(rets) < 2:
        return None
    return float(rets.std(ddof=1) * math.sqrt(252))


# ── Simulation step ───────────────────────────────────────────────────────────

def open_strangle(conn, today, spot, vol):
    expiry = next_expiry(today)
    T = t_years(today, expiry)
    kc = round(spot * (1 + OTM_PCT) / STRIKE_STEP) * STRIKE_STEP
    kp = round(spot * (1 - OTM_PCT) / STRIKE_STEP) * STRIKE_STEP
    c = bs_price(spot, kc, T, vol, RISK_FREE, "call")
    p = bs_price(spot, kp, T, vol, RISK_FREE, "put")

    gross_pts    = c + p
    spread_pts   = SPREAD_PCT * c + SPREAD_PCT * p
    entry_spread = spread_pts * LOT_SIZE
    entry_stat   = statutory(c) + statutory(p)
    premium_net  = (gross_pts - spread_pts) * LOT_SIZE - entry_stat

    conn.execute(
        "INSERT INTO cycles (open_date, expiry, spot_open, call_strike, put_strike, "
        "premium_gross, entry_spread, entry_stat, premium_net, status) "
        "VALUES (?,?,?,?,?,?,?,?,?, 'open')",
        (today.isoformat(), expiry.isoformat(), round(spot, 2), kc, kp,
         round(gross_pts * LOT_SIZE, 2), round(entry_spread, 2),
         round(entry_stat, 2), round(premium_net, 2)))
    conn.commit()


def step(conn, closes):
    """Advance the sim to the latest NIFTY day. Returns the day processed."""
    today = closes.index[-1].date()
    spot  = float(closes.iloc[-1])
    vol   = realized_vol(closes)
    if vol is None:
        print("  Not enough NIFTY history for realized vol.")
        return None
    move  = float(closes.iloc[-1] / closes.iloc[-2] - 1) if len(closes) > 1 else 0.0

    cyc = open_cycle(conn)

    # No open position → open a fresh strangle for the next monthly expiry.
    if cyc is None:
        open_strangle(conn, today, spot, vol)
        cyc = open_cycle(conn)
        # (a brand-new cycle is also marked below)

    expiry = date.fromisoformat(cyc["expiry"])
    T = t_years(today, expiry)
    cval = bs_price(spot, cyc["call_strike"], T, vol, RISK_FREE, "call")
    pval = bs_price(spot, cyc["put_strike"],  T, vol, RISK_FREE, "put")
    # Mark to mid: what's left to buy back vs the net premium already banked.
    open_pnl = cyc["premium_net"] - (cval + pval) * LOT_SIZE

    conn.execute(
        "INSERT OR REPLACE INTO marks (cycle_id, mark_date, spot, daily_move, "
        "call_val, put_val, open_pnl) VALUES (?,?,?,?,?,?,?)",
        (cyc["id"], today.isoformat(), round(spot, 2), round(move, 4),
         round(cval, 2), round(pval, 2), round(open_pnl, 2)))
    if abs(move) >= VOL_EVENT:
        conn.execute("UPDATE cycles SET vol_event = 1 WHERE id = ?", (cyc["id"],))

    # Settle at expiry (intrinsic, cash-settled, no spread).
    if today >= expiry:
        intrinsic = (max(0.0, spot - cyc["call_strike"]) +
                     max(0.0, cyc["put_strike"] - spot)) * LOT_SIZE
        pnl = cyc["premium_net"] - intrinsic
        _close(conn, cyc, today, "EXPIRY", pnl)
    # Stop: loss hit 2x premium → close early, paying the exit spread.
    elif open_pnl <= -STOP_MULT * cyc["premium_net"]:
        exit_spread = SPREAD_PCT * (cval + pval) * LOT_SIZE
        pnl = open_pnl - exit_spread
        _close(conn, cyc, today, "STOP", pnl)

    conn.commit()
    return today


def _close(conn, cyc, today, reason, pnl):
    conn.execute(
        "UPDATE cycles SET status='closed', close_date=?, close_reason=?, settle_pnl=? "
        "WHERE id = ?", (today.isoformat(), reason, round(pnl, 2), cyc["id"]))
    conn.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (pnl,))


def settle_now(conn):
    """Manually close the open strangle NOW at its current mark, paying the exit
    spread (as a real close would). Realises the open P&L into the book — used by
    the dashboard 'settle books' action. No-op if nothing is open."""
    cyc = open_cycle(conn)
    if cyc is None:
        print("  No open strangle to settle.")
        return None
    closes = nifty_daily()
    if closes is None or len(closes) < 2:
        print("  No NIFTY data — cannot settle.")
        return None
    today = closes.index[-1].date()
    spot = float(closes.iloc[-1])
    vol = realized_vol(closes)
    expiry = date.fromisoformat(cyc["expiry"])
    T = t_years(today, expiry)
    cval = bs_price(spot, cyc["call_strike"], T, vol, RISK_FREE, "call")
    pval = bs_price(spot, cyc["put_strike"], T, vol, RISK_FREE, "put")
    open_pnl = cyc["premium_net"] - (cval + pval) * LOT_SIZE
    exit_spread = SPREAD_PCT * (cval + pval) * LOT_SIZE
    pnl = open_pnl - exit_spread
    _close(conn, cyc, today, "SETTLE", pnl)
    conn.commit()
    print(f"  Settled strangle at mark ({today}) → realised {rupees(pnl)}")
    return pnl


# ── Reporting ─────────────────────────────────────────────────────────────────

def rupees(x):
    return f"₹{x:,.2f}"


def report(conn):
    W = 88
    print(f"\n{'='*W}")
    print("  NIFTY SHORT-STRANGLE — FORWARD PAPER (model-priced, SIMULATED, no orders)")
    print(f"{'='*W}")

    cyc = open_cycle(conn)
    if cyc:
        last = conn.execute(
            "SELECT * FROM marks WHERE cycle_id=? ORDER BY mark_date DESC LIMIT 1",
            (cyc["id"],)).fetchone()
        opnl = last["open_pnl"] if last else 0.0
        s = "+" if opnl >= 0 else ""
        print(f"  OPEN  expiry {cyc['expiry']}   strikes {cyc['put_strike']:.0f}P / "
              f"{cyc['call_strike']:.0f}C")
        print(f"        premium banked {rupees(cyc['premium_net'])}  "
              f"(entry spread {rupees(cyc['entry_spread'])})   "
              f"mark-to-model P&L {s}{rupees(opnl)}")
    else:
        print("  Flat (no open position).")

    agg = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(settle_pnl),0) net, "
        "SUM(CASE WHEN settle_pnl>0 THEN 1 ELSE 0 END) wins, "
        "SUM(CASE WHEN close_reason='STOP' THEN 1 ELSE 0 END) stops "
        "FROM cycles WHERE status='closed'").fetchone()
    n = agg["n"]
    print(f"\n  Closed cycles: {n}", end="")
    if n:
        s = "+" if agg["net"] >= 0 else ""
        print(f"   realized {s}{rupees(agg['net'])}   "
              f"wins {agg['wins']}/{n}   stops {agg['stops']}")
    else:
        print()

    # Verdict gate: a vol event must have occurred while short.
    had_event = conn.execute(
        "SELECT 1 FROM marks WHERE ABS(daily_move) >= ? LIMIT 1", (VOL_EVENT,)
    ).fetchone() is not None
    print(f"\n  {'─'*W}")
    if had_event:
        print("  A volatility event (>=4% NIFTY day) has occurred while short — "
              "results are now meaningful to read.")
    else:
        print("  VERDICT: INCONCLUSIVE — awaiting a volatility event "
              "(>=4% NIFTY day while short). Quiet months prove nothing.")
    print(f"  Book equity: {rupees(get_cash(conn))}   "
          f"(started {rupees(CAPITAL)})")
    print(f"{'='*W}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    conn = db_connect()
    if "--settle" in sys.argv:
        print("NIFTY short-strangle — SETTLE open position")
        settle_now(conn)
        report(conn)
        conn.close()
        return
    print("NIFTY short-strangle forward paper simulator")
    print("Fetching NIFTY daily (yfinance ^NSEI)...", end=" ", flush=True)
    closes = nifty_daily()
    print("done")
    if closes is None or len(closes) < RV_WINDOW + 2:
        print("  Not enough NIFTY data — nothing to simulate.")
        conn.close()
        return

    day = step(conn, closes)
    if day is not None:
        print(f"  Advanced to {day}.")
    report(conn)
    conn.close()


if __name__ == "__main__":
    main()
