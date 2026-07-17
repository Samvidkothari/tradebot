"""
review.py — Read-only summary of the paper-trading history in portfolio.db.

STANDALONE MANUAL TOOL — not imported by anything and not part of any automated
pipeline (run it by hand when you want a one-off review of the low-vol paper
book). The dashboard's Paper Trader tab + the Tear Sheets / Risk pages now cover
most of this; kept because it is a convenient read-only CLI summary.

Run any time (you don't need to have run paper_trader.py today). It:
  1. Lists every simulated trade (fills)
  2. Reconstructs a DAILY paper equity curve by replaying fills against fetched
     daily closes (handles the low-vol model's partial trims / top-ups)
  3. Values current open positions at live prices
  4. Compares the paper portfolio's return + drawdown to NIFTY 50 over the
     same window

Read-only: it never writes to the database and never places orders.
Matches the low-volatility paper_trader (monthly rebalances, partial fills).
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


def _ticker_for(symbol):
    """yfinance ticker for a NIFTY symbol (same rule as paper_trader.universe)."""
    return f"{symbol}.NS"


# ── Equity-curve reconstruction (daily, partial-fill aware) ───────────────────

def build_price_panel(symbols):
    """Fetch a daily-close panel (index=dates, cols=symbols) for the given names."""
    closes = {}
    for s in symbols:
        ser = fetch_live(_ticker_for(s))
        if ser is not None and not ser.empty:
            closes[s] = ser
    return pd.DataFrame(closes).sort_index() if closes else pd.DataFrame()


def reconstruct_equity_curve(conn):
    """
    Rebuild total equity (cash + holdings) for every trading day from the first
    fill onward, using only stored fills + fetched daily closes:
      • cash(d)     = STARTING_CAPITAL + cumulative fill cash_deltas with run_date <= d
      • qty(d, sym) = cumulative (BUY qty − SELL qty) with run_date <= d
      • holdings(d) = Σ qty(d,sym) × close(d,sym)   (ffill'd; gaps carried forward)
    Returns a list of (date_str, equity) at daily granularity.
    """
    fills = conn.execute(
        "SELECT run_date, symbol, side, qty, cash_delta FROM fills ORDER BY id"
    ).fetchall()
    if not fills:
        return []

    symbols   = sorted({f["symbol"] for f in fills})
    panel     = build_price_panel(symbols).ffill()
    first_day = min(f["run_date"] for f in fills)
    if panel.empty:
        return []

    days = panel.index[panel.index >= pd.Timestamp(first_day)]
    curve = []
    for d in days:
        d_str = str(d.date())
        cash  = STARTING_CAPITAL
        qty   = {}
        for f in fills:
            if f["run_date"] > d_str:
                break
            cash += f["cash_delta"]
            delta = f["qty"] if f["side"] == "BUY" else -f["qty"]
            qty[f["symbol"]] = qty.get(f["symbol"], 0) + delta

        holdings_value = 0.0
        for sym, q in qty.items():
            if q > 0 and sym in panel.columns:
                px = panel.loc[d, sym]
                if pd.notna(px):
                    holdings_value += q * px
        curve.append((d_str, cash + holdings_value))

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
        ltp = float(live.iloc[-1]) if live is not None and not live.empty else p["avg_price"]
        value = p["qty"] * ltp
        unreal = (ltp - p["avg_price"]) * p["qty"]
        holdings_value += value
        sign = "+" if unreal >= 0 else ""
        print(f"  {p['symbol']:<12} {p['qty']:>5} {p['avg_price']:>10.2f} "
              f"{ltp:>10.2f} {rupees(value):>14} {sign}{rupees(unreal):>14}")
    return holdings_value


def print_performance(conn, curve, live_holdings_value):
    cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()["cash"]
    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl), 0) AS r FROM fills WHERE side='SELL'"
    ).fetchone()["r"]

    if not curve:
        print("\n── Performance ──\n  (no fills yet — nothing to chart)")
        return

    start_date = curve[0][0]
    end_date   = str(date.today())

    # Live current equity (cash + live-valued holdings)
    cur_equity = cash + live_holdings_value
    paper_ret  = cur_equity / STARTING_CAPITAL - 1
    dd         = max_drawdown(curve + [(end_date, cur_equity)])

    # NIFTY 50 buy-and-hold over the same calendar window
    nifty = fetch_live("^NSEI")
    nifty_ret = None
    if nifty is not None and not nifty.empty:
        window = nifty[nifty.index >= pd.Timestamp(start_date)]
        if len(window) >= 2:
            nifty_ret = float(window.iloc[-1] / window.iloc[0] - 1)

    print("\n── Performance ──")
    print(f"  Window:          {start_date} → {end_date}  "
          f"({len(curve)} trading day(s))")
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

    n_fills = conn.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"]
    if n_fills == 0:
        print("No trades logged yet — run  python paper_trader.py  first.")
        return

    print("═══ Paper-Trading Review (low-volatility strategy) ═══")

    print_trades(conn)
    live_holdings = print_positions(conn)
    curve = reconstruct_equity_curve(conn)
    print_performance(conn, curve, live_holdings)
    print()

    conn.close()


if __name__ == "__main__":
    main()
