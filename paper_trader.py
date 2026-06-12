"""
paper_trader.py — Daily SIMULATED paper-trading loop.

Run once per trading day, after the close. It:
  1. Pulls fresh daily candles (yfinance — no Kite token needed)
  2. Asks strategy.generate_signal() for each stock's target state
  3. Reconciles target vs current paper holdings and simulates fills
  4. Logs every signal + fill + position to SQLite (portfolio.db)
  5. Prints the current paper portfolio

THIS PLACES NO REAL ORDERS. Every "fill" is a row in a local database.
Nothing in this file touches Kite's order API. It is safe to run daily.

Running it twice in one day is harmless: reconciliation is state-based,
so the second run sees you already hold the target and does nothing.
"""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from strategy import generate_signal, generate_signals, FAST_PERIOD, SLOW_PERIOD
from backtest import COST_ENTRY, COST_EXIT   # reuse the exact cost model

# ── Config ────────────────────────────────────────────────────────────────────
TICKERS = {
    "RELIANCE":   "RELIANCE.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "INFY":       "INFY.NS",
    "TCS":        "TCS.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "LT":         "LT.NS",
    "SBIN":       "SBIN.NS",
    "BHARTIARTL": "BHARTIARTL.NS",
    "ITC":        "ITC.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
}
STARTING_CAPITAL = 1_000_000          # ₹10,00,000 paper money
LOOKBACK_DAYS    = 400                 # enough history for the 50-day SMA
DB_PATH          = Path(__file__).parent / "portfolio.db"
# ─────────────────────────────────────────────────────────────────────────────


# ── Money formatting (Indian style, ₹) ────────────────────────────────────────

def rupees(x):
    return f"₹{x:,.2f}"


# ── Database ──────────────────────────────────────────────────────────────────

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id   INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS positions (
            symbol    TEXT PRIMARY KEY,
            qty       INTEGER NOT NULL,
            avg_price REAL    NOT NULL,
            opened    TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date  TEXT, symbol TEXT, signal TEXT,
            close     REAL, sma_fast REAL, sma_slow REAL
        );
        CREATE TABLE IF NOT EXISTS fills (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date   TEXT, symbol TEXT, side TEXT,
            qty        INTEGER, price REAL, cost REAL,
            cash_delta REAL, realised_pnl REAL
        );
    """)
    # First-ever run: seed the cash balance
    row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)",
                     (STARTING_CAPITAL,))
        conn.commit()
        print(f"Initialised paper account with {rupees(STARTING_CAPITAL)}\n")
    return conn


def get_cash(conn):
    return conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()["cash"]


def set_cash(conn, amount):
    conn.execute("UPDATE account SET cash = ? WHERE id = 1", (amount,))


def get_position(conn, symbol):
    return conn.execute("SELECT * FROM positions WHERE symbol = ?",
                        (symbol,)).fetchone()


# ── Live data ─────────────────────────────────────────────────────────────────

def fetch_live(ticker):
    """Pull recent daily candles in-memory (does NOT touch the backtest CSVs)."""
    end   = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    raw = yf.Ticker(ticker).history(
        start=start, end=end + timedelta(days=1),
        interval="1d", auto_adjust=True,
    )
    if raw.empty:
        return None
    df = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.columns = ["open", "high", "low", "close", "volume"]
    df.index.name = "date"
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    return df.sort_values("date").reset_index(drop=True)


# ── Trade simulation ──────────────────────────────────────────────────────────

def simulate_buy(conn, symbol, price, run_date):
    """Deploy up to one equal-weight slice of starting capital into `symbol`."""
    cash      = get_cash(conn)
    slice_amt = STARTING_CAPITAL / len(TICKERS)
    budget    = min(slice_amt, cash)
    qty       = int(budget // price)

    if qty < 1:
        return None  # not enough cash for even one share

    gross = qty * price
    cost  = gross * COST_ENTRY
    conn.execute(
        "INSERT INTO positions (symbol, qty, avg_price, opened) VALUES (?,?,?,?)",
        (symbol, qty, price, run_date),
    )
    set_cash(conn, cash - gross - cost)
    conn.execute(
        "INSERT INTO fills (run_date, symbol, side, qty, price, cost, "
        "cash_delta, realised_pnl) VALUES (?,?,?,?,?,?,?,?)",
        (run_date, symbol, "BUY", qty, price, cost, -(gross + cost), None),
    )
    return {"qty": qty, "price": price, "cost": cost}


def simulate_sell(conn, symbol, price, run_date, pos):
    """Close the entire position in `symbol`."""
    qty   = pos["qty"]
    gross = qty * price
    cost  = gross * COST_EXIT
    realised = (price - pos["avg_price"]) * qty - cost

    conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
    set_cash(conn, get_cash(conn) + gross - cost)
    conn.execute(
        "INSERT INTO fills (run_date, symbol, side, qty, price, cost, "
        "cash_delta, realised_pnl) VALUES (?,?,?,?,?,?,?,?)",
        (run_date, symbol, "SELL", qty, price, cost, gross - cost, realised),
    )
    return {"qty": qty, "price": price, "cost": cost, "realised": realised}


# ── Main daily run ────────────────────────────────────────────────────────────

def main():
    run_date = str(date.today())
    conn = db_connect()

    print(f"Paper-trading run — {run_date}")
    print(f"Strategy: SMA {FAST_PERIOD}/{SLOW_PERIOD} crossover (target-state)\n")
    print(f"  {'Symbol':<12} {'Signal':<6} {'Close':>10}  Action")
    print(f"  {'─'*60}")

    latest_close = {}

    for symbol, ticker in TICKERS.items():
        df = fetch_live(ticker)
        if df is None or df.empty:
            print(f"  {symbol:<12} {'—':<6} {'n/a':>10}  data fetch failed")
            continue

        signal = generate_signal(df)
        last   = df.iloc[-1]
        price  = float(last["close"])
        latest_close[symbol] = price

        # Log the signal (audit trail) — recompute SMAs for the log row
        sig_df = generate_signals(df).iloc[-1]
        conn.execute(
            "INSERT INTO signals (run_date, symbol, signal, close, sma_fast, sma_slow) "
            "VALUES (?,?,?,?,?,?)",
            (run_date, symbol, signal, price,
             float(sig_df["sma_fast"]) if pd.notna(sig_df["sma_fast"]) else None,
             float(sig_df["sma_slow"]) if pd.notna(sig_df["sma_slow"]) else None),
        )

        pos = get_position(conn, symbol)
        action = "hold"

        if signal == "BUY" and pos is None:
            fill = simulate_buy(conn, symbol, price, run_date)
            action = (f"BUY  {fill['qty']} @ {rupees(price)}"
                      if fill else "BUY signal — insufficient cash")
        elif signal == "SELL" and pos is not None:
            fill = simulate_sell(conn, symbol, price, run_date, pos)
            pnl_sign = "+" if fill["realised"] >= 0 else ""
            action = (f"SELL {fill['qty']} @ {rupees(price)}  "
                      f"(realised {pnl_sign}{rupees(fill['realised'])})")
        elif signal == "BUY" and pos is not None:
            action = "already long — hold"
        elif signal == "SELL" and pos is None:
            action = "flat — nothing to sell"

        print(f"  {symbol:<12} {signal:<6} {price:>10.2f}  {action}")

    conn.commit()
    print_portfolio(conn, latest_close)
    conn.close()


def print_portfolio(conn, latest_close):
    cash = get_cash(conn)
    positions = conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall()

    print(f"\n  {'─'*60}")
    print(f"  Open positions:")
    holdings_value = 0.0
    if not positions:
        print("    (none — fully in cash)")
    else:
        print(f"    {'Symbol':<12} {'Qty':>6} {'Avg':>10} {'LTP':>10} "
              f"{'Value':>13} {'Unreal P&L':>14}")
        for p in positions:
            ltp   = latest_close.get(p["symbol"], p["avg_price"])
            value = p["qty"] * ltp
            unreal = (ltp - p["avg_price"]) * p["qty"]
            holdings_value += value
            sign = "+" if unreal >= 0 else ""
            print(f"    {p['symbol']:<12} {p['qty']:>6} {p['avg_price']:>10.2f} "
                  f"{ltp:>10.2f} {rupees(value):>13} {sign}{rupees(unreal):>13}")

    # Realised P&L to date
    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl), 0) AS r FROM fills WHERE side = 'SELL'"
    ).fetchone()["r"]

    total_equity = cash + holdings_value
    total_return = total_equity - STARTING_CAPITAL

    print(f"\n  {'─'*60}")
    print(f"  Cash:            {rupees(cash):>18}")
    print(f"  Holdings value:  {rupees(holdings_value):>18}")
    print(f"  Total equity:    {rupees(total_equity):>18}")
    sign = "+" if total_return >= 0 else ""
    print(f"  Total return:    {sign}{rupees(total_return):>17}  "
          f"({sign}{total_return/STARTING_CAPITAL*100:.2f}%)")
    sign = "+" if realised >= 0 else ""
    print(f"  Realised P&L:    {sign}{rupees(realised):>17}")
    print()


if __name__ == "__main__":
    main()
