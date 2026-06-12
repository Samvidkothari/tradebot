"""
review.py — Read-only summary of the paper-trading history in portfolio.db.

Run any time (you don't need to have run paper_trader.py today). It:
  1. Lists every simulated trade (fills)
  2. Reconstructs the paper equity curve from stored data, run by run
  3. Values current open positions at live prices
  4. Compares the paper portfolio's return to NIFTY 50 over the same window

Read-only: it never writes to the database and never places orders.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

from paper_trader import STARTING_CAPITAL, fetch_live, rupees

DB_PATH = Path(__file__).parent / "portfolio.db"


def load_db():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Equity-curve reconstruction ───────────────────────────────────────────────

def reconstruct_equity_curve(conn):
    """
    Rebuild total equity (cash + holdings) as of each run date, using only
    data already stored in the DB:
      • cash      = STARTING_CAPITAL + cumulative fill cash_deltas up to that date
      • holdings  = open positions on that date, valued at that date's signal close
    Returns a list of (run_date, equity) sorted by date.
    """
    run_dates = [r["run_date"] for r in conn.execute(
        "SELECT DISTINCT run_date FROM signals ORDER BY run_date").fetchall()]
    if not run_dates:
        return []

    fills = conn.execute(
        "SELECT run_date, symbol, side, qty, cash_delta FROM fills "
        "ORDER BY id").fetchall()

    # close price per (run_date, symbol) for valuing holdings on each date
    closes = {(r["run_date"], r["symbol"]): r["close"]
              for r in conn.execute("SELECT run_date, symbol, close FROM signals")}

    curve = []
    for d in run_dates:
        cash = STARTING_CAPITAL
        positions = {}   # symbol -> qty
        for f in fills:
            if f["run_date"] > d:
                break
            cash += f["cash_delta"]
            if f["side"] == "BUY":
                positions[f["symbol"]] = f["qty"]
            elif f["side"] == "SELL":
                positions.pop(f["symbol"], None)

        holdings_value = 0.0
        for sym, qty in positions.items():
            price = closes.get((d, sym))
            if price is not None:
                holdings_value += qty * price
        curve.append((d, cash + holdings_value))

    return curve


def max_drawdown(curve):
    """Largest peak-to-trough drop in the equity curve, as a negative fraction."""
    peak = None
    worst = 0.0
    for _, eq in curve:
        peak = eq if peak is None else max(peak, eq)
        if peak > 0:
            worst = min(worst, (eq - peak) / peak)
    return worst


# ── Sections ──────────────────────────────────────────────────────────────────

def print_trades(conn):
    fills = conn.execute(
        "SELECT run_date, symbol, side, qty, price, cost, realised_pnl "
        "FROM fills ORDER BY id").fetchall()

    print("\n── Trade history ──")
    if not fills:
        print("  No trades yet.")
        return

    print(f"  {'Date':<12} {'Symbol':<12} {'Side':<5} {'Qty':>5} "
          f"{'Price':>10} {'Cost':>9} {'Realised P&L':>15}")
    for f in fills:
        if f["realised_pnl"] is None:
            pnl = "—"
        else:
            sign = "+" if f["realised_pnl"] >= 0 else ""
            pnl = f"{sign}{rupees(f['realised_pnl'])}"
        print(f"  {f['run_date']:<12} {f['symbol']:<12} {f['side']:<5} "
              f"{f['qty']:>5} {f['price']:>10.2f} {f['cost']:>9.2f} {pnl:>15}")


def print_positions(conn):
    positions = conn.execute(
        "SELECT * FROM positions ORDER BY symbol").fetchall()

    print("\n── Open positions (live prices) ──")
    if not positions:
        print("  (none — fully in cash)")
        return 0.0

    print(f"  {'Symbol':<12} {'Qty':>5} {'Avg':>10} {'LTP':>10} "
          f"{'Value':>14} {'Unreal P&L':>15}")
    holdings_value = 0.0
    for p in positions:
        live = fetch_live(_ticker_for(p["symbol"]))
        ltp = float(live.iloc[-1]["close"]) if live is not None else p["avg_price"]
        value = p["qty"] * ltp
        unreal = (ltp - p["avg_price"]) * p["qty"]
        holdings_value += value
        sign = "+" if unreal >= 0 else ""
        print(f"  {p['symbol']:<12} {p['qty']:>5} {p['avg_price']:>10.2f} "
              f"{ltp:>10.2f} {rupees(value):>14} {sign}{rupees(unreal):>14}")
    return holdings_value


def _ticker_for(symbol):
    from paper_trader import TICKERS
    return TICKERS[symbol]


def print_performance(conn, curve, live_holdings_value):
    cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()["cash"]
    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl), 0) AS r FROM fills WHERE side='SELL'"
    ).fetchone()["r"]

    start_date = curve[0][0]
    end_date   = str(date.today())
    n_runs     = len(curve)

    # Live current equity (cash + live-valued holdings)
    cur_equity = cash + live_holdings_value
    paper_ret  = cur_equity / STARTING_CAPITAL - 1
    dd         = max_drawdown(curve + [(end_date, cur_equity)])

    # NIFTY 50 buy-and-hold over the same calendar window
    nifty = fetch_live("^NSEI")
    nifty_ret = None
    if nifty is not None and not nifty.empty:
        window = nifty[nifty["date"] >= pd.Timestamp(start_date)]
        if len(window) >= 2:
            nifty_ret = float(window["close"].iloc[-1] / window["close"].iloc[0] - 1)

    print("\n── Performance ──")
    print(f"  Window:          {start_date} → {end_date}  ({n_runs} run(s) logged)")
    print(f"  Starting capital:{rupees(STARTING_CAPITAL):>18}")
    print(f"  Current equity:  {rupees(cur_equity):>18}")
    s = "+" if paper_ret >= 0 else ""
    print(f"  Paper return:    {s}{paper_ret*100:>16.2f}%")
    print(f"  Max drawdown:    {dd*100:>17.2f}%")
    s = "+" if realised >= 0 else ""
    print(f"  Realised P&L:    {s}{rupees(realised):>17}")

    if nifty_ret is not None:
        s = "+" if nifty_ret >= 0 else ""
        print(f"  NIFTY 50 B&H:    {s}{nifty_ret*100:>16.2f}%  (same window)")
        diff = paper_ret - nifty_ret
        verdict = "ahead of" if diff > 0 else "behind"
        print(f"\n  Paper portfolio is {verdict} buy-and-hold by "
              f"{abs(diff)*100:.2f} pts over this window.")
    else:
        print("  NIFTY 50 B&H:    n/a (window too short for a comparison yet)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    conn = load_db()
    if conn is None:
        print("No portfolio.db yet — run  python paper_trader.py  first.")
        return

    runs = conn.execute("SELECT COUNT(DISTINCT run_date) AS n FROM signals").fetchone()["n"]
    if runs == 0:
        print("No runs logged yet — run  python paper_trader.py  first.")
        return

    print("═══ Paper-Trading Review ═══")

    print_trades(conn)
    live_holdings = print_positions(conn)
    curve = reconstruct_equity_curve(conn)
    print_performance(conn, curve, live_holdings)
    print()

    conn.close()


if __name__ == "__main__":
    main()
