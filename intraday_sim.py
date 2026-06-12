"""
intraday_sim.py — Intraday Opening-Range Breakout PAPER simulator.

Implements exactly strategies/SPEC_intraday.md (pre-registered, commit eb0aae6).

Each run simulates ONE trading day for a small set of liquid stocks:
  • the first 15 minutes (three 5-min bars) define an opening range
  • the first 5-min bar that closes outside the range opens a simulated trade
    (long on an up-break, short on a down-break) — one trade per symbol per day
  • stop = opposite edge of the range; target = 1× the range; otherwise the
    position is squared off at the 15:15 bar
  • everything is closed by the close — NOTHING is held overnight

THIS PLACES NO REAL ORDERS. Every fill is a row in intraday.db. Nothing here
touches Kite's order API. It is safe to run unattended.

Usage:
  python intraday_sim.py            # simulate the most recent available day
  python intraday_sim.py 2026-06-11 # simulate a specific date (must be in range)
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── Pre-registered parameters (SPEC_intraday.md — do not tune to results) ──────
UNIVERSE = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
            "SBIN", "AXISBANK", "ITC", "LT", "BHARTIARTL"]

CAPITAL        = 1_000_000     # paper rupees, separate from the low-vol book
MAX_CONCURRENT = 5             # at most 5 open intraday positions at once
NOTIONAL       = CAPITAL / MAX_CONCURRENT
OR_BARS        = 3             # first three 5-min bars = 09:15–09:30
SQUAREOFF      = "15:15"       # bar time at which any open position is closed
INTERVAL       = "5m"
DB_PATH        = Path(__file__).parent / "intraday.db"

# Intraday (MIS) cost model — rupees, per leg unless noted
BROKERAGE_RATE = 0.0003        # 0.03% ...
BROKERAGE_CAP  = 20.0          # ... capped at ₹20 per leg
STT_SELL       = 0.00025       # 0.025%, SELL leg only
EXCH_TXN       = 0.0000297     # 0.00297% per leg (NSE)
SEBI           = 0.000001      # 0.0001% per leg
STAMP_BUY      = 0.00003       # 0.003%, BUY leg only
GST            = 0.18          # on (brokerage + exch + sebi)
SLIPPAGE       = 0.0005        # 0.05% per leg
# ─────────────────────────────────────────────────────────────────────────────


def leg_cost(notional, is_buy):
    """Rupee cost of one leg (buy or sell) under the intraday MIS model."""
    brokerage = min(BROKERAGE_CAP, BROKERAGE_RATE * notional)
    txn       = EXCH_TXN * notional
    sebi      = SEBI * notional
    gst       = GST * (brokerage + txn + sebi)
    stt       = 0.0 if is_buy else STT_SELL * notional
    stamp     = STAMP_BUY * notional if is_buy else 0.0
    slip      = SLIPPAGE * notional
    return brokerage + txn + sebi + gst + stt + stamp + slip


# ── Database ──────────────────────────────────────────────────────────────────

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id   INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS days (
            trade_date TEXT PRIMARY KEY,
            n_trades   INTEGER,
            gross_pnl  REAL,
            costs      REAL,
            net_pnl    REAL,
            simulated  TEXT
        );
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date  TEXT, symbol TEXT, side TEXT,
            entry_time  TEXT, entry_px REAL,
            exit_time   TEXT, exit_px  REAL,
            qty         INTEGER, gross_pnl REAL, costs REAL, net_pnl REAL,
            exit_reason TEXT
        );
    """)
    if conn.execute("SELECT cash FROM account WHERE id = 1").fetchone() is None:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)", (CAPITAL,))
        conn.commit()
    return conn


# ── Data ──────────────────────────────────────────────────────────────────────

def fetch_intraday(symbol):
    """5-min bars for the last month (IST), columns open/high/low/close/volume."""
    raw = yf.Ticker(f"{symbol}.NS").history(period="1mo", interval=INTERVAL)
    if raw.empty:
        return None
    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index = pd.to_datetime(df.index)          # tz-aware IST from yfinance
    return df


def day_slice(df, day):
    """Bars belonging to calendar date `day` (a 'YYYY-MM-DD' string), sorted."""
    d = df[df.index.normalize().astype(str).str.startswith(day)]
    return d.sort_index()


# ── The ORB rule for a single symbol-day ──────────────────────────────────────

def simulate_symbol_day(bars):
    """
    Apply the opening-range-breakout rule to one symbol's bars for one day.
    Returns a trade dict (without sizing/costs) or None if no breakout occurred.
    """
    if len(bars) < OR_BARS + 1:
        return None

    opening = bars.iloc[:OR_BARS]
    or_high = float(opening["high"].max())
    or_low  = float(opening["low"].min())
    or_rng  = or_high - or_low
    if or_rng <= 0:
        return None

    after = bars.iloc[OR_BARS:]

    # Find the first breakout bar (by close), long or short.
    side = entry_px = entry_time = None
    for ts, bar in after.iterrows():
        c = float(bar["close"])
        if c > or_high:
            side, entry_px, entry_time = "LONG", c, ts
            break
        if c < or_low:
            side, entry_px, entry_time = "SHORT", c, ts
            break
    if side is None:
        return None

    if side == "LONG":
        stop, target = or_low, entry_px + or_rng
    else:
        stop, target = or_high, entry_px - or_rng

    # Manage the trade on subsequent bars (stop checked before target).
    rest = after[after.index > entry_time]
    for ts, bar in rest.iterrows():
        c = float(bar["close"])
        if str(ts.strftime("%H:%M")) >= SQUAREOFF:
            return _mk(side, entry_time, entry_px, ts, c, "EOD")
        if side == "LONG":
            if c <= stop:    return _mk(side, entry_time, entry_px, ts, c, "STOP")
            if c >= target:  return _mk(side, entry_time, entry_px, ts, c, "TARGET")
        else:
            if c >= stop:    return _mk(side, entry_time, entry_px, ts, c, "STOP")
            if c <= target:  return _mk(side, entry_time, entry_px, ts, c, "TARGET")

    # No stop/target/square-off hit (e.g. breakout on the very last bar):
    # close at the final available bar.
    last_ts = rest.index[-1] if len(rest) else entry_time
    last_px = float(rest.iloc[-1]["close"]) if len(rest) else entry_px
    return _mk(side, entry_time, entry_px, last_ts, last_px, "EOD")


def _mk(side, et, ep, xt, xp, reason):
    return dict(side=side, entry_time=et, entry_px=ep,
                exit_time=xt, exit_px=xp, exit_reason=reason)


def size_and_cost(trade):
    """Add qty + rupee P&L and costs to a raw trade dict (per spec §5–6)."""
    qty = int(NOTIONAL // trade["entry_px"])
    if qty < 1:
        return None
    entry_notional = qty * trade["entry_px"]
    exit_notional  = qty * trade["exit_px"]

    if trade["side"] == "LONG":          # buy then sell
        gross = (trade["exit_px"] - trade["entry_px"]) * qty
        costs = leg_cost(entry_notional, is_buy=True) + leg_cost(exit_notional, is_buy=False)
    else:                                # sell then buy back
        gross = (trade["entry_px"] - trade["exit_px"]) * qty
        costs = leg_cost(entry_notional, is_buy=False) + leg_cost(exit_notional, is_buy=True)

    trade.update(qty=qty, gross_pnl=gross, costs=costs, net_pnl=gross - costs)
    return trade


# ── Orchestration for one day ─────────────────────────────────────────────────

def run_day(conn, target_day=None):
    """Simulate one trading day across the universe. Returns (day, trades)."""
    panels = {}
    for sym in UNIVERSE:
        df = fetch_intraday(sym)
        if df is not None and not df.empty:
            panels[sym] = df
    if not panels:
        print("  No intraday data returned — nothing to simulate.")
        return None, []

    # Resolve which day to simulate (default: latest available across symbols).
    all_days = sorted({d for df in panels.values()
                       for d in df.index.normalize().astype(str).str[:10].unique()})
    if not all_days:
        return None, []
    day = target_day or all_days[-1]
    if day not in all_days:
        print(f"  {day} not in available intraday range "
              f"({all_days[0]} … {all_days[-1]}).")
        return None, []

    if conn.execute("SELECT 1 FROM days WHERE trade_date = ?", (day,)).fetchone():
        print(f"  {day} already simulated — skipping (idempotent).")
        return None, []

    # Build raw trades, then admit them in time order under MAX_CONCURRENT.
    raw = []
    for sym, df in panels.items():
        bars = day_slice(df, day)
        t = simulate_symbol_day(bars)
        if t:
            t["symbol"] = sym
            raw.append(t)
    raw.sort(key=lambda t: t["entry_time"])      # earliest breakouts get priority

    trades = []
    for t in raw:
        if len(trades) >= MAX_CONCURRENT:
            break
        sized = size_and_cost(t)
        if sized:
            trades.append(sized)
    return day, trades


def persist(conn, day, trades):
    gross = sum(t["gross_pnl"] for t in trades)
    costs = sum(t["costs"] for t in trades)
    net   = gross - costs
    for t in trades:
        conn.execute(
            "INSERT INTO trades (trade_date, symbol, side, entry_time, entry_px, "
            "exit_time, exit_px, qty, gross_pnl, costs, net_pnl, exit_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, t["symbol"], t["side"],
             t["entry_time"].strftime("%H:%M"), round(t["entry_px"], 2),
             t["exit_time"].strftime("%H:%M"),  round(t["exit_px"], 2),
             t["qty"], round(t["gross_pnl"], 2), round(t["costs"], 2),
             round(t["net_pnl"], 2), t["exit_reason"]))
    conn.execute(
        "INSERT INTO days (trade_date, n_trades, gross_pnl, costs, net_pnl, simulated) "
        "VALUES (?,?,?,?,?,?)",
        (day, len(trades), round(gross, 2), round(costs, 2), round(net, 2),
         str(date.today())))
    conn.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (net,))
    conn.commit()


# ── Reporting ─────────────────────────────────────────────────────────────────

def rupees(x):
    return f"₹{x:,.2f}"


def print_day(day, trades, conn):
    print(f"\n{'='*86}")
    print(f"  INTRADAY ORB PAPER SIM — {day}   (SIMULATED, no real orders)")
    print(f"{'='*86}")
    if not trades:
        print("  No breakout trades for this day.")
    else:
        print(f"  {'Symbol':<11} {'Side':<6} {'Entry':>6} {'@':>9} "
              f"{'Exit':>6} {'@':>9} {'Qty':>5} {'Net P&L':>13}  Why")
        print(f"  {'─'*84}")
        for t in trades:
            s = "+" if t["net_pnl"] >= 0 else ""
            print(f"  {t['symbol']:<11} {t['side']:<6} "
                  f"{t['entry_time'].strftime('%H:%M'):>6} {t['entry_px']:>9.2f} "
                  f"{t['exit_time'].strftime('%H:%M'):>6} {t['exit_px']:>9.2f} "
                  f"{t['qty']:>5} {s}{rupees(t['net_pnl']):>12}  {t['exit_reason']}")
        gross = sum(t["gross_pnl"] for t in trades)
        costs = sum(t["costs"] for t in trades)
        net   = gross - costs
        wins  = sum(1 for t in trades if t["net_pnl"] > 0)
        print(f"  {'─'*84}")
        s = "+" if net >= 0 else ""
        print(f"  Trades {len(trades)}   Wins {wins}/{len(trades)}   "
              f"Gross {rupees(gross)}   Costs {rupees(costs)}   "
              f"Net {s}{rupees(net)}")

    cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
    cum  = conn.execute("SELECT COALESCE(SUM(net_pnl),0) s FROM days").fetchone()["s"]
    print(f"\n  Book equity: {rupees(cash)}   "
          f"(cumulative net since start: {'+' if cum>=0 else ''}{rupees(cum)})")
    print(f"{'='*86}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    target_day = sys.argv[1] if len(sys.argv) > 1 else None
    conn = db_connect()
    print(f"Intraday ORB paper simulator — universe of {len(UNIVERSE)} liquid names")
    print("Fetching 5-min bars (yfinance)...", end=" ", flush=True)
    day, trades = run_day(conn, target_day)
    print("done")
    if day is not None:                  # a fresh, in-range day (0+ trades)
        persist(conn, day, trades)       # records the day even with no trades
        print_day(day, trades, conn)
    conn.close()


if __name__ == "__main__":
    main()
