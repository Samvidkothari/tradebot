"""
condor_sim.py — Forward DEFINED-RISK iron-condor PAPER simulator (NIFTY).

Implements exactly strategies/SPEC_condor.md (pre-registered, commit a845e84),
which rests on strategies/THESIS_condor.md. It is the naked short strangle with
its unlimited tail risk capped by two bought wings, run side-by-side with the
strangle (options_sim.py) to answer one head-to-head question: does giving up
the tail risk cost us the edge after honest, spread-inclusive costs?

FORWARD, model-priced, paper-only. Each run, for the most recent NIFTY day:
  • if flat, open a monthly iron condor:
        SELL a 4%-OTM call + 4%-OTM put   (the premium-harvesting bodies)
        BUY  a 6%-OTM call + 6%-OTM put   (the protective wings)
    pricing every leg with Black–Scholes using 20-day realized vol as an IV
    proxy, with a deliberately HARSH 10%/leg spread haircut charged on ALL FOUR
    legs (the doubled bleed vs the strangle is the crux — see SPEC §4);
  • if a position is open, mark it to model and flag a vol event on a >=4% day;
  • settle at intrinsic on the last-Thursday expiry. NO early stop — the bought
    wings ARE the risk cap, so max loss is structurally finite (SPEC §3).

THIS PLACES NO REAL ORDERS. Every fill is a row in condor.db. It is paper-only,
and per SPEC/THESIS it must NEVER be wired to fully-autonomous live trading.

Usage:
  python condor_sim.py             # advance the sim to the latest NIFTY day
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

import notify_telegram  # fail-soft Telegram push (no-op unless .env configured)

# ── Pre-registered parameters (SPEC_condor.md — do not tune to results) ────────
OTM_PCT     = 0.04        # short (body) strikes 4% OTM
WING_PCT    = 0.06        # long (wing) strikes 6% OTM  → ~2%-of-spot wing width
STRIKE_STEP = 50          # round strikes to nearest 50
LOT_SIZE    = 75          # NIFTY lot
CAPITAL     = PAPER_CAPITAL   # paper book
RV_WINDOW   = 20          # trading days for realized vol
RISK_FREE   = 0.065
SPREAD_PCT  = 0.10        # HARSH bid-ask haircut, per leg, per transaction
OPEN_MIN_DTE = 21         # v2 (SPEC §v2): open only on a monthly expiry >= 21
                          # calendar days out — no near-worthless stubs
VOL_EVENT   = 0.04        # |NIFTY daily move| that counts as a volatility event
# NOTE: no STOP_MULT — defined risk is the cap; an early stop would be a
# redundant extra parameter (SPEC §3).

# Light statutory costs on premium turnover (rupees) — secondary to the spread.
# Kept identical to options_sim.py so the head-to-head comparison is fair.
BROKERAGE_CAP = 20.0
STT_RATE      = 0.000625  # 0.0625% on sell-side option premium
EXCH_TXN      = 0.0003503 # ~0.03503% of premium (NSE options)
GST           = 0.18
DB_PATH       = Path(__file__).parent / "condor.db"
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
            sc_strike REAL, sp_strike REAL, lc_strike REAL, lp_strike REAL,
            premium_gross REAL, entry_spread REAL, entry_stat REAL, premium_net REAL,
            max_loss REAL,
            status TEXT, close_date TEXT, close_reason TEXT, settle_pnl REAL,
            vol_event INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS marks (
            cycle_id INTEGER, mark_date TEXT, spot REAL, daily_move REAL,
            sc_val REAL, sp_val REAL, lc_val REAL, lp_val REAL, open_pnl REAL,
            PRIMARY KEY (cycle_id, mark_date));
        CREATE TABLE IF NOT EXISTS precommit (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, committed TEXT NOT NULL);
    """)
    # Pre-committed stress-test criteria — INSERT OR IGNORE: written ONCE,
    # never updated by code, so the judgment rules cannot drift after the fact.
    for k, v in (
        ("vol_event_threshold", f"{VOL_EVENT}"),
        ("event_definition", "|NIFTY daily move| >= 4% while a cycle is open"),
        ("stop_rule", "none — the bought wings ARE the risk management; "
                      "worst case is the structural max_loss stored per cycle"),
        ("win_criteria", "WIN if, through a cycle containing a vol event, the "
                         "realised loss stays within max_loss AND the avoided "
                         "tail (vs the naked strangle run head-to-head on the "
                         "same days) exceeds the premium foregone to the wings"),
        ("loss_criteria", "LOSS if the event cycle realises at/near max_loss "
                          "while the strangle fares no worse — the insurance "
                          "cost was not worth it"),
        ("verdict_gate", "INCONCLUSIVE until >= 1 vol event occurs while "
                         "open; judged only head-to-head vs the strangle"),
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

def open_condor(conn, today, spot, vol):
    expiry = next_expiry(today)
    T = t_years(today, expiry)
    # Bodies (sold) 4% OTM; wings (bought) 6% OTM. Strikes rounded to STRIKE_STEP.
    sc = round(spot * (1 + OTM_PCT)  / STRIKE_STEP) * STRIKE_STEP   # short call
    sp = round(spot * (1 - OTM_PCT)  / STRIKE_STEP) * STRIKE_STEP   # short put
    lc = round(spot * (1 + WING_PCT) / STRIKE_STEP) * STRIKE_STEP   # long call (wing)
    lp = round(spot * (1 - WING_PCT) / STRIKE_STEP) * STRIKE_STEP   # long put (wing)

    sc_p = bs_price(spot, sc, T, vol, RISK_FREE, "call")
    sp_p = bs_price(spot, sp, T, vol, RISK_FREE, "put")
    lc_p = bs_price(spot, lc, T, vol, RISK_FREE, "call")
    lp_p = bs_price(spot, lp, T, vol, RISK_FREE, "put")

    # Net credit in points = premium sold − premium paid for the wings.
    gross_pts  = (sc_p + sp_p) - (lc_p + lp_p)
    # Harsh spread haircut on ALL FOUR legs (we cross the spread on every leg).
    spread_pts = SPREAD_PCT * (sc_p + sp_p + lc_p + lp_p)
    entry_spread = spread_pts * LOT_SIZE
    entry_stat   = statutory(sc_p) + statutory(sp_p) + statutory(lc_p) + statutory(lp_p)
    premium_net  = (gross_pts - spread_pts) * LOT_SIZE - entry_stat

    # Structural max loss: only one side can be breached at expiry. The wing
    # width on each side caps the loss; subtract the credit kept.
    width = max(lc - sc, sp - lp) * LOT_SIZE
    max_loss = width - premium_net

    conn.execute(
        "INSERT INTO cycles (open_date, expiry, spot_open, sc_strike, sp_strike, "
        "lc_strike, lp_strike, premium_gross, entry_spread, entry_stat, premium_net, "
        "max_loss, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open')",
        (today.isoformat(), expiry.isoformat(), round(spot, 2), sc, sp, lc, lp,
         round(gross_pts * LOT_SIZE, 2), round(entry_spread, 2),
         round(entry_stat, 2), round(premium_net, 2), round(max_loss, 2)))
    conn.commit()
    notify_telegram.notify(
        f"🟢 Condor OPEN — NIFTY, expiry {expiry}\n"
        f"bodies {sp:.0f}P / {sc:.0f}C sold · wings {lp:.0f}P / {lc:.0f}C bought\n"
        f"net credit {rupees(premium_net)} · max loss capped {rupees(max_loss)}  [PAPER]")


def _unwind_cost_pts(sc_v, sp_v, lc_v, lp_v):
    """Net points to close: buy back the shorts, sell the wings."""
    return (sc_v + sp_v) - (lc_v + lp_v)


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

    # No open position → open a fresh condor for the next monthly expiry.
    if cyc is None:
        open_condor(conn, today, spot, vol)
        cyc = open_cycle(conn)

    expiry = date.fromisoformat(cyc["expiry"])
    T = t_years(today, expiry)
    sc_v = bs_price(spot, cyc["sc_strike"], T, vol, RISK_FREE, "call")
    sp_v = bs_price(spot, cyc["sp_strike"], T, vol, RISK_FREE, "put")
    lc_v = bs_price(spot, cyc["lc_strike"], T, vol, RISK_FREE, "call")
    lp_v = bs_price(spot, cyc["lp_strike"], T, vol, RISK_FREE, "put")
    # Mark to mid: net premium banked minus what it costs to unwind now.
    open_pnl = cyc["premium_net"] - _unwind_cost_pts(sc_v, sp_v, lc_v, lp_v) * LOT_SIZE

    conn.execute(
        "INSERT OR REPLACE INTO marks (cycle_id, mark_date, spot, daily_move, "
        "sc_val, sp_val, lc_val, lp_val, open_pnl) VALUES (?,?,?,?,?,?,?,?,?)",
        (cyc["id"], today.isoformat(), round(spot, 2), round(move, 4),
         round(sc_v, 2), round(sp_v, 2), round(lc_v, 2), round(lp_v, 2),
         round(open_pnl, 2)))
    if abs(move) >= VOL_EVENT:
        conn.execute("UPDATE cycles SET vol_event = 1 WHERE id = ?", (cyc["id"],))
        # The stress day this sim exists to observe — a >=4% NIFTY move while open.
        notify_telegram.notify(
            f"⚡ VOL EVENT — NIFTY {move:+.1%} on {today}, condor OPEN\n"
            f"bodies {cyc['sp_strike']:.0f}P / {cyc['sc_strike']:.0f}C · "
            f"mark-to-model P&L {'+' if open_pnl >= 0 else ''}{rupees(open_pnl)} "
            f"(max loss {rupees(cyc['max_loss'])})  [PAPER]")

    # Settle at expiry (intrinsic on all four legs, cash-settled, no spread).
    if today >= expiry:
        sc_i = max(0.0, spot - cyc["sc_strike"])   # we owe on short call
        sp_i = max(0.0, cyc["sp_strike"] - spot)   # we owe on short put
        lc_i = max(0.0, spot - cyc["lc_strike"])   # we collect on long call
        lp_i = max(0.0, cyc["lp_strike"] - spot)   # we collect on long put
        net_owed = ((sc_i + sp_i) - (lc_i + lp_i)) * LOT_SIZE
        pnl = cyc["premium_net"] - net_owed
        _close(conn, cyc, today, "EXPIRY", pnl)

    conn.commit()
    return today


def _close(conn, cyc, today, reason, pnl):
    conn.execute(
        "UPDATE cycles SET status='closed', close_date=?, close_reason=?, settle_pnl=? "
        "WHERE id = ?", (today.isoformat(), reason, round(pnl, 2), cyc["id"]))
    conn.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (pnl,))
    icon = "🎯" if pnl >= 0 else "🔻"
    notify_telegram.notify(
        f"{icon} Condor {reason} — NIFTY\n"
        f"expiry {cyc['expiry']} · bodies {cyc['sp_strike']:.0f}P / "
        f"{cyc['sc_strike']:.0f}C\n"
        f"settled P&L {'+' if pnl >= 0 else ''}{rupees(pnl)}  [PAPER]")


def settle_now(conn):
    """Manually close the open condor NOW at its current mark, paying the exit
    spread on all four legs. Realises the open P&L into the book — used by the
    dashboard 'settle books' action. No-op if nothing is open."""
    cyc = open_cycle(conn)
    if cyc is None:
        print("  No open condor to settle.")
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
    sc_v = bs_price(spot, cyc["sc_strike"], T, vol, RISK_FREE, "call")
    sp_v = bs_price(spot, cyc["sp_strike"], T, vol, RISK_FREE, "put")
    lc_v = bs_price(spot, cyc["lc_strike"], T, vol, RISK_FREE, "call")
    lp_v = bs_price(spot, cyc["lp_strike"], T, vol, RISK_FREE, "put")
    open_pnl = cyc["premium_net"] - _unwind_cost_pts(sc_v, sp_v, lc_v, lp_v) * LOT_SIZE
    exit_spread = SPREAD_PCT * (sc_v + sp_v + lc_v + lp_v) * LOT_SIZE
    pnl = open_pnl - exit_spread
    _close(conn, cyc, today, "SETTLE", pnl)
    conn.commit()
    print(f"  Settled condor at mark ({today}) → realised {rupees(pnl)}")
    return pnl


# ── Reporting ─────────────────────────────────────────────────────────────────

def rupees(x):
    return f"₹{x:,.2f}"


def report(conn):
    W = 88
    print(f"\n{'='*W}")
    print("  NIFTY IRON CONDOR — FORWARD PAPER (defined-risk, model-priced, SIMULATED)")
    print(f"{'='*W}")

    cyc = open_cycle(conn)
    if cyc:
        last = conn.execute(
            "SELECT * FROM marks WHERE cycle_id=? ORDER BY mark_date DESC LIMIT 1",
            (cyc["id"],)).fetchone()
        opnl = last["open_pnl"] if last else 0.0
        s = "+" if opnl >= 0 else ""
        print(f"  OPEN  expiry {cyc['expiry']}")
        print(f"        bodies  {cyc['sp_strike']:.0f}P / {cyc['sc_strike']:.0f}C "
              f"(sold)    wings {cyc['lp_strike']:.0f}P / {cyc['lc_strike']:.0f}C (bought)")
        print(f"        net credit {rupees(cyc['premium_net'])}  "
              f"(entry spread {rupees(cyc['entry_spread'])})   "
              f"max loss capped {rupees(cyc['max_loss'])}")
        print(f"        mark-to-model P&L {s}{rupees(opnl)}")
    else:
        print("  Flat (no open position).")

    agg = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(settle_pnl),0) net, "
        "SUM(CASE WHEN settle_pnl>0 THEN 1 ELSE 0 END) wins "
        "FROM cycles WHERE status='closed'").fetchone()
    n = agg["n"]
    print(f"\n  Closed cycles: {n}", end="")
    if n:
        s = "+" if agg["net"] >= 0 else ""
        print(f"   realized {s}{rupees(agg['net'])}   wins {agg['wins']}/{n}")
    else:
        print()

    # Verdict gate: a vol event must have occurred while short.
    had_event = conn.execute(
        "SELECT 1 FROM marks WHERE ABS(daily_move) >= ? LIMIT 1", (VOL_EVENT,)
    ).fetchone() is not None
    print(f"\n  {'─'*W}")
    if had_event:
        print("  A volatility event (>=4% NIFTY day) has occurred while short — "
              "results are now meaningful (compare head-to-head vs the strangle).")
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
        print("NIFTY iron-condor — SETTLE open position")
        settle_now(conn)
        report(conn)
        conn.close()
        return
    print("NIFTY iron-condor forward paper simulator")
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
