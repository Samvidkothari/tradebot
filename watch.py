"""
watch.py — Holdings and positions viewer.
Run after login.py each morning.

NOTE — Live quotes (print_quotes) require the paid Kite Connect plan.
       The function is kept below but not called. To enable it:
         1. Upgrade your app to a paid Connect plan
         2. Uncomment the print_quotes(kite) line at the bottom
         3. Edit WATCHLIST to your instruments
"""

import sys

from kiteconnect.exceptions import TokenException, NetworkException, DataException, PermissionException
from kite_client import load_kite

# ── Watchlist (used when quotes are enabled) ──────────────────────────────────
#   Format: "EXCHANGE:TRADINGSYMBOL" — e.g. "NSE:RELIANCE", "NSE:NIFTY 50"
WATCHLIST = [
    "NSE:RELIANCE",
    "NSE:HDFCBANK",
    "NSE:INFY",
    "NSE:TCS",
    "NSE:NIFTY 50",
]
# ─────────────────────────────────────────────────────────────────────────────


def safe_call(fn, *args, **kwargs):
    """Run a Kite API call and exit with a readable message on known errors."""
    try:
        return fn(*args, **kwargs)
    except TokenException:
        sys.exit("\nToken expired or invalid.\n→ Run:  python login.py")
    except PermissionException:
        sys.exit("\nPermission denied for that API call.\n"
                 "The free Personal plan does not include market-data/quote access.\n"
                 "→ Upgrade to a paid Connect plan to enable quotes.")
    except NetworkException as e:
        sys.exit(f"\nNetwork error — check your internet connection.\nDetail: {e}")
    except DataException as e:
        sys.exit(f"\nBad data from Kite API: {e}")
    except Exception as e:
        sys.exit(f"\nUnexpected error: {e}")


def print_quotes(kite):
    """
    Fetch and display live quotes for WATCHLIST instruments.
    Requires the paid Kite Connect plan (quote permission).
    Not called by default — see the note at the top of this file.
    """
    quotes = safe_call(kite.quote, WATCHLIST)

    print("\n┌─ Live Quotes " + "─" * 79 + "┐")
    print(
        f"  {'Symbol':<18} {'LTP':>10} {'Change':>9}  "
        f"{'Volume':>11}  {'Open':>9} {'High':>9} {'Low':>9} {'Prev Close':>10}"
    )
    print("  " + "─" * 89)

    for symbol, data in quotes.items():
        ltp   = data["last_price"]
        ohlc  = data["ohlc"]
        prev  = ohlc["close"]
        chg   = ((ltp - prev) / prev * 100) if prev else 0
        vol   = data.get("volume", 0)
        name  = symbol.split(":")[1]
        arrow = "▲" if chg >= 0 else "▼"

        print(
            f"  {name:<18} "
            f"{ltp:>10.2f} "
            f"{arrow}{abs(chg):>7.2f}%  "
            f"{vol:>11,}  "
            f"{ohlc['open']:>9.2f} "
            f"{ohlc['high']:>9.2f} "
            f"{ohlc['low']:>9.2f} "
            f"{prev:>10.2f}"
        )
    print("└" + "─" * 92 + "┘")


def print_holdings(kite):
    """
    Display holdings with avg price, last price from the holdings API,
    and calculated P&L. The holdings API returns last_price even on the
    free plan, so P&L figures here are always current.
    """
    holdings = safe_call(kite.holdings)

    print("\n┌─ Holdings " + "─" * 82 + "┐")

    if not holdings:
        print("  No holdings found.")
        print("└" + "─" * 92 + "┘")
        return

    print(f"  {'Stock':<18} {'Qty':>6}  {'Avg Price':>10}  {'LTP':>10}  {'P&L (₹)':>12}  {'P&L %':>8}")
    print("  " + "─" * 73)

    total_pnl = 0.0
    for h in holdings:
        qty       = h["quantity"]
        avg       = h["average_price"]
        ltp       = h["last_price"]
        pnl       = (ltp - avg) * qty
        pct       = ((ltp - avg) / avg * 100) if avg else 0
        total_pnl += pnl
        sign      = "+" if pnl >= 0 else ""

        print(
            f"  {h['tradingsymbol']:<18} "
            f"{qty:>6}  "
            f"{avg:>10.2f}  "
            f"{ltp:>10.2f}  "
            f"{sign}{pnl:>11.2f}  "
            f"{sign}{pct:>7.2f}%"
        )

    print("  " + "─" * 73)
    sign = "+" if total_pnl >= 0 else ""
    print(f"  {'Total P&L':<49} {sign}{total_pnl:>11.2f}")
    print("└" + "─" * 92 + "┘")


def print_positions(kite):
    """Display all open (non-zero) net positions."""
    data = safe_call(kite.positions)
    net  = [p for p in data.get("net", []) if p["quantity"] != 0]

    print("\n┌─ Open Positions " + "─" * 76 + "┐")

    if not net:
        print("  No open positions.")
        print("└" + "─" * 92 + "┘")
        return

    print(f"  {'Symbol':<18} {'Qty':>6}  {'Avg Price':>10}  {'LTP':>10}  {'P&L (₹)':>12}")
    print("  " + "─" * 62)

    for p in net:
        qty  = p["quantity"]
        avg  = p["average_price"]
        ltp  = p["last_price"]
        pnl  = p.get("pnl", 0.0)
        sign = "+" if pnl >= 0 else ""

        print(
            f"  {p['tradingsymbol']:<18} "
            f"{qty:>6}  "
            f"{avg:>10.2f}  "
            f"{ltp:>10.2f}  "
            f"{sign}{pnl:>11.2f}"
        )
    print("└" + "─" * 92 + "┘")


if __name__ == "__main__":
    kite = load_kite()   # exits cleanly if token is missing or stale

    # print_quotes(kite)  # ← uncomment when on a paid Connect plan
    print_holdings(kite)
    print_positions(kite)
    print()
