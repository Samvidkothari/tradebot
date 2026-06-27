"""
intraday_sim.py — Intraday PAPER simulator (multi-strategy).

Runs two pre-registered intraday strategies on the same data each day:
  • ORB  — Opening-Range Breakout        (strategies/SPEC_intraday.md, eb0aae6)
  • VWAP — VWAP mean-reversion           (strategies/SPEC_vwap.md,     3ee37e0)

Each has its OWN paper book (separate cash), but they share one fetch, one
universe, one cost model, and one ledger (intraday.db, every row tagged with
its strategy) so they can be compared on identical days. Positions are intraday
only — everything squared off by 15:15, NOTHING held overnight.

THIS PLACES NO REAL ORDERS. Every fill is a row in intraday.db. Nothing here
touches Kite's order API. It is safe to run unattended.

Usage:
  python intraday_sim.py            # simulate the most recent available day
  python intraday_sim.py 2026-06-11 # simulate a specific date (must be in range)
"""

from config import PAPER_CAPITAL
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── Shared parameters (pre-registered — do not tune to results) ────────────────
UNIVERSE = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
            "SBIN", "AXISBANK", "ITC", "LT", "BHARTIARTL"]

CAPITAL        = PAPER_CAPITAL     # paper rupees per strategy book
MAX_CONCURRENT = 5             # at most 5 open positions at once, per strategy
NOTIONAL       = CAPITAL / MAX_CONCURRENT
SQUAREOFF      = "15:15"       # bar time at which any open position is closed
INTERVAL       = "5m"
DB_PATH        = Path(__file__).parent / "intraday.db"

# ORB params (SPEC_intraday.md)
OR_BARS    = 3                 # first three 5-min bars = 09:15–09:30

# VWAP params (SPEC_vwap.md)
WARMUP_BARS = 6                # ignore first ~30 min so VWAP is stable
BAND        = 0.005            # 0.5% stretch from VWAP to enter
STOP_BAND   = 0.012            # 1.2% adverse stretch = stop

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


def _mk(side, et, ep, xt, xp, reason):
    return dict(side=side, entry_time=et, entry_px=ep,
                exit_time=xt, exit_px=xp, exit_reason=reason)


# ── Strategy 1: Opening-Range Breakout ────────────────────────────────────────

def signal_orb(bars):
    """First breakout of the 09:15–09:30 range; stop = opposite edge,
    target = 1× range, square-off 15:15. Returns a raw trade dict or None."""
    if len(bars) < OR_BARS + 1:
        return None

    opening = bars.iloc[:OR_BARS]
    or_high = float(opening["high"].max())
    or_low  = float(opening["low"].min())
    or_rng  = or_high - or_low
    if or_rng <= 0:
        return None

    after = bars.iloc[OR_BARS:]

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

    rest = after[after.index > entry_time]
    for ts, bar in rest.iterrows():
        c = float(bar["close"])
        if ts.strftime("%H:%M") >= SQUAREOFF:
            return _mk(side, entry_time, entry_px, ts, c, "EOD")
        if side == "LONG":
            if c <= stop:    return _mk(side, entry_time, entry_px, ts, c, "STOP")
            if c >= target:  return _mk(side, entry_time, entry_px, ts, c, "TARGET")
        else:
            if c >= stop:    return _mk(side, entry_time, entry_px, ts, c, "STOP")
            if c <= target:  return _mk(side, entry_time, entry_px, ts, c, "TARGET")

    last_ts = rest.index[-1] if len(rest) else entry_time
    last_px = float(rest.iloc[-1]["close"]) if len(rest) else entry_px
    return _mk(side, entry_time, entry_px, last_ts, last_px, "EOD")


# ── Strategy 2: VWAP mean-reversion ───────────────────────────────────────────

def signal_vwap(bars):
    """Fade the first stretch >= BAND from cumulative VWAP; target = revert to
    VWAP, stop = STOP_BAND adverse, square-off 15:15. Raw trade dict or None."""
    if len(bars) < WARMUP_BARS + 1:
        return None

    typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    pv      = (typical * bars["volume"]).cumsum()
    volc    = bars["volume"].cumsum()
    vwap    = (pv / volc).where(volc > 0)

    idx    = list(bars.index)
    closes = bars["close"].astype(float).tolist()
    vw     = vwap.tolist()
    n      = len(bars)

    # Entry: first bar (after warm-up) stretched >= BAND from VWAP.
    side = entry_px = entry_time = None
    entry_i = None
    for i in range(WARMUP_BARS, n):
        w = vw[i]
        if w is None or pd.isna(w) or w <= 0:
            continue
        dev = closes[i] / w - 1.0
        if dev <= -BAND:
            side, entry_px, entry_time, entry_i = "LONG", closes[i], idx[i], i
            break
        if dev >= BAND:
            side, entry_px, entry_time, entry_i = "SHORT", closes[i], idx[i], i
            break
    if side is None:
        return None

    # Manage: stop checked before target (revert to VWAP); square-off at 15:15.
    for i in range(entry_i + 1, n):
        ts = idx[i]
        c  = closes[i]
        w  = vw[i]
        if ts.strftime("%H:%M") >= SQUAREOFF:
            return _mk(side, entry_time, entry_px, ts, c, "EOD")
        if w is None or pd.isna(w) or w <= 0:
            continue
        dev = c / w - 1.0
        if side == "LONG":
            if dev <= -STOP_BAND: return _mk(side, entry_time, entry_px, ts, c, "STOP")
            if dev >= 0:          return _mk(side, entry_time, entry_px, ts, c, "VWAP")
        else:
            if dev >= STOP_BAND:  return _mk(side, entry_time, entry_px, ts, c, "STOP")
            if dev <= 0:          return _mk(side, entry_time, entry_px, ts, c, "VWAP")

    last_ts = idx[-1]
    last_px = closes[-1]
    return _mk(side, entry_time, entry_px, last_ts, last_px, "EOD")


STRATEGIES = {"ORB": signal_orb, "VWAP": signal_vwap}


# ── Sizing & costs (shared) ───────────────────────────────────────────────────

def size_and_cost(trade):
    """Add qty + rupee P&L and costs to a raw trade dict."""
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


# ── Database (multi-strategy schema + migration) ──────────────────────────────

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def exists(t):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (t,)).fetchone() is not None

    def cols(t):
        return [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]

    # account — keyed by strategy
    if not exists("account"):
        conn.execute("CREATE TABLE account (strategy TEXT PRIMARY KEY, cash REAL NOT NULL)")
    elif "strategy" not in cols("account"):     # migrate old account(id, cash)
        conn.executescript("""
            ALTER TABLE account RENAME TO account_old;
            CREATE TABLE account (strategy TEXT PRIMARY KEY, cash REAL NOT NULL);
            INSERT INTO account (strategy, cash) SELECT 'ORB', cash FROM account_old WHERE id = 1;
            DROP TABLE account_old;
        """)

    # days — composite (trade_date, strategy)
    if not exists("days"):
        conn.execute("""CREATE TABLE days (
            trade_date TEXT, strategy TEXT, n_trades INTEGER,
            gross_pnl REAL, costs REAL, net_pnl REAL, simulated TEXT,
            PRIMARY KEY (trade_date, strategy))""")
    elif "strategy" not in cols("days"):        # migrate old days(PK trade_date)
        conn.executescript("""
            ALTER TABLE days RENAME TO days_old;
            CREATE TABLE days (
                trade_date TEXT, strategy TEXT, n_trades INTEGER,
                gross_pnl REAL, costs REAL, net_pnl REAL, simulated TEXT,
                PRIMARY KEY (trade_date, strategy));
            INSERT INTO days (trade_date, strategy, n_trades, gross_pnl, costs, net_pnl, simulated)
                SELECT trade_date, 'ORB', n_trades, gross_pnl, costs, net_pnl, simulated FROM days_old;
            DROP TABLE days_old;
        """)

    # trades — + strategy column
    if not exists("trades"):
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT, strategy TEXT, symbol TEXT, side TEXT,
            entry_time TEXT, entry_px REAL, exit_time TEXT, exit_px REAL,
            qty INTEGER, gross_pnl REAL, costs REAL, net_pnl REAL, exit_reason TEXT)""")
    elif "strategy" not in cols("trades"):
        conn.execute("ALTER TABLE trades ADD COLUMN strategy TEXT NOT NULL DEFAULT 'ORB'")

    # Seed each strategy's paper book once.
    for s in STRATEGIES:
        if conn.execute("SELECT 1 FROM account WHERE strategy = ?", (s,)).fetchone() is None:
            conn.execute("INSERT INTO account (strategy, cash) VALUES (?, ?)", (s, CAPITAL))
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
    df.index = pd.to_datetime(df.index)
    return df


def day_slice(df, day):
    """Bars belonging to calendar date `day` ('YYYY-MM-DD'), sorted."""
    return df[df.index.normalize().astype(str).str.startswith(day)].sort_index()


def fetch_all_panels():
    panels = {}
    for sym in UNIVERSE:
        df = fetch_intraday(sym)
        if df is not None and not df.empty:
            panels[sym] = df
    return panels


def resolve_day(panels, target_day):
    all_days = sorted({d for df in panels.values()
                       for d in df.index.normalize().astype(str).str[:10].unique()})
    if not all_days:
        return None
    day = target_day or all_days[-1]
    if day not in all_days:
        print(f"  {day} not in available intraday range "
              f"({all_days[0]} … {all_days[-1]}).")
        return None
    return day


# ── Run one strategy for one day ──────────────────────────────────────────────

def run_strategy_day(conn, panels, day, name, fn):
    """Simulate `name` for `day`. Returns trades list, or None if already done."""
    if conn.execute("SELECT 1 FROM days WHERE trade_date = ? AND strategy = ?",
                    (day, name)).fetchone():
        print(f"  [{name}] {day} already simulated — skipping (idempotent).")
        return None

    raw = []
    for sym, df in panels.items():
        t = fn(day_slice(df, day))
        if t:
            t["symbol"] = sym
            raw.append(t)
    raw.sort(key=lambda t: t["entry_time"])      # earliest signals get priority

    trades = []
    for t in raw:
        if len(trades) >= MAX_CONCURRENT:
            break
        sized = size_and_cost(t)
        if sized:
            trades.append(sized)

    persist(conn, day, name, trades)
    return trades


def persist(conn, day, name, trades):
    gross = sum(t["gross_pnl"] for t in trades)
    costs = sum(t["costs"] for t in trades)
    net   = gross - costs
    for t in trades:
        conn.execute(
            "INSERT INTO trades (trade_date, strategy, symbol, side, entry_time, "
            "entry_px, exit_time, exit_px, qty, gross_pnl, costs, net_pnl, exit_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (day, name, t["symbol"], t["side"],
             t["entry_time"].strftime("%H:%M"), round(t["entry_px"], 2),
             t["exit_time"].strftime("%H:%M"),  round(t["exit_px"], 2),
             t["qty"], round(t["gross_pnl"], 2), round(t["costs"], 2),
             round(t["net_pnl"], 2), t["exit_reason"]))
    conn.execute(
        "INSERT INTO days (trade_date, strategy, n_trades, gross_pnl, costs, net_pnl, simulated) "
        "VALUES (?,?,?,?,?,?,?)",
        (day, name, len(trades), round(gross, 2), round(costs, 2), round(net, 2),
         str(date.today())))
    conn.execute("UPDATE account SET cash = cash + ? WHERE strategy = ?", (net, name))
    conn.commit()


# ── Reporting ─────────────────────────────────────────────────────────────────

def rupees(x):
    return f"₹{x:,.2f}"


def print_day(day, name, trades, conn):
    print(f"\n{'='*86}")
    print(f"  INTRADAY {name} PAPER SIM — {day}   (SIMULATED, no real orders)")
    print(f"{'='*86}")
    if not trades:
        print("  No trades for this day.")
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

    row = conn.execute("SELECT cash FROM account WHERE strategy=?", (name,)).fetchone()
    cum = conn.execute("SELECT COALESCE(SUM(net_pnl),0) s FROM days WHERE strategy=?",
                       (name,)).fetchone()["s"]
    print(f"\n  [{name}] book equity: {rupees(row['cash'])}   "
          f"(cumulative net: {'+' if cum>=0 else ''}{rupees(cum)})")
    print(f"{'='*86}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    target_day = sys.argv[1] if len(sys.argv) > 1 else None
    conn = db_connect()
    print(f"Intraday paper simulator — strategies: {', '.join(STRATEGIES)} "
          f"on {len(UNIVERSE)} liquid names")
    print("Fetching 5-min bars (yfinance)...", end=" ", flush=True)
    panels = fetch_all_panels()
    print("done")
    if not panels:
        print("  No intraday data returned — nothing to simulate.")
        conn.close()
        return

    day = resolve_day(panels, target_day)
    if day is None:
        conn.close()
        return

    for name, fn in STRATEGIES.items():
        trades = run_strategy_day(conn, panels, day, name, fn)
        if trades is not None:
            print_day(day, name, trades, conn)
    conn.close()


if __name__ == "__main__":
    main()
