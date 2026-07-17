"""
intraday_mark.py — hourly MARK + RISK pass over the low-vol paper book.

Scope (deliberate, per the cost-gate doctrine): this job trades NOTHING.
Intraday (ORB/VWAP) was retired 2026-06-26 because a thin gross edge did not
survive realistic costs, and SPEC_intraday_cost_gate.md pre-commits that no
higher-frequency strategy earns live code until it PASSES cost_gate.evaluate().
So the value of running hourly is protection latency, not alpha:

  1. Mark  — refresh LTPs for held names from 60m bars (market_data.py, the
             throttled/retrying layer) and mark the book to market.
  2. Risk  — run risk_governor.mark() so the kill switch / daily-loss brake
             reacts within the hour instead of waiting for the nightly run.
             If the switch is ON and risk_limits.json sets auto_liquidate,
             the same liquidation path paper_trader uses fires here too
             (SELLs are risk-reducing and always allowed; they use the same
             delivery-cost model — COST_EXIT — as every other simulated sell).
  3. Log   — append one row to intraday_marks (portfolio.db) so the dashboard
             can chart intraday equity and prove the governor was watching.

Turnover added by this job: zero (governor liquidation aside). Rebalancing
stays monthly in paper_trader.py. PAPER ONLY — no order API is ever touched.

Run manually:  python intraday_mark.py        (skips on non-session days)
Scheduled by:  scheduler.py (hourly, 10:15–15:15 IST, Mon–Fri)
"""

from __future__ import annotations

from datetime import date, datetime

import market_data
import paper_trader
import risk_governor
from trading_calendar import TradingCalendar


def _log_mark(conn, ts: str, gov: dict, n_held: int, n_priced: int) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS intraday_marks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        TEXT NOT NULL,
            equity    REAL, drawdown REAL, daily_ret REAL,
            killed    INTEGER, n_held INTEGER, n_priced INTEGER,
            reason    TEXT
        )""")
    conn.execute(
        "INSERT INTO intraday_marks (ts, equity, drawdown, daily_ret, killed, "
        "n_held, n_priced, reason) VALUES (?,?,?,?,?,?,?,?)",
        (ts, gov.get("equity"), gov.get("drawdown"), gov.get("daily_ret"),
         int(bool(gov.get("killed"))), n_held, n_priced, gov.get("reason")),
    )


def main() -> int:
    """One hourly pass. Returns 0 on success/skip, 1 on hard failure."""
    today = date.today()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Session guard — same fail-open doctrine as run_paper_bot.sh: a broken
    # calendar must never block the run; a known non-session day skips it.
    try:
        if not TradingCalendar().is_session(today):
            print(f"[{ts}] non-session day — hourly mark skipped.")
            return 0
    except Exception as e:
        print(f"[{ts}] calendar check failed ({e}) — proceeding fail-open.")

    conn = paper_trader.db_connect()
    try:
        held = [r["symbol"] for r in
                conn.execute("SELECT symbol FROM positions ORDER BY symbol")]
        tickers = {s: f"{s}.NS" for s in held}

        prices, failed = market_data.fetch_last_prices(tickers)
        if failed:
            print(f"[{ts}] ⚠ no price for {len(failed)}: {', '.join(failed)} "
                  f"(governor falls back to avg cost for these)")

        # Governor evaluates on EVERY pass — hold days and hours included.
        gov = risk_governor.mark(conn, prices)

        # Honor auto-liquidation exactly like the daily run does.
        paper_trader._governor_liquidate(conn, gov, prices, str(today))

        _log_mark(conn, ts, gov, n_held=len(held), n_priced=len(prices))
        conn.commit()

        eq = gov.get("equity")
        dd = gov.get("drawdown")
        eq_s = f"₹{eq:,.0f}" if eq is not None else "n/a"
        dd_s = f", drawdown {dd:.2%}" if dd is not None else ""
        print(f"[{ts}] marked {len(prices)}/{len(held)} names — "
              f"equity {eq_s}{dd_s} — governor: {gov.get('reason')}")
        return 0
    except Exception as e:
        print(f"[{ts}] hourly mark FAILED: {e}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
