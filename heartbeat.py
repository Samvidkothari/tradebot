"""
heartbeat.py — one daily "the books are alive" Telegram status ping.

WHY: the per-engine alerts (paper_trader / options_sim / condor_sim / vwap_bot)
are EVENT-DRIVEN — they only push on an open, close, vol event or rebalance. On
a quiet day nothing fires, which is correct but indistinguishable from "the bot
didn't run." This script is the heartbeat: it reads each paper book's SQLite DB
(NO network, NO order side effects) and sends ONE compact status summary, so you
get a daily confirmation even when nothing traded.

Run as the LAST step of run_paper_bot.sh (after the sims have marked their books
for the day). Fail-soft: a missing/locked DB for one book is caught and noted;
the other books and the send still go through. No-op on the Telegram side unless
.env has TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (notify_telegram contract).

Usage:
  python heartbeat.py            # compose + send the daily status
  python heartbeat.py --dry-run  # print the message, send nothing
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import notify_telegram
from config import PAPER_CAPITAL

# DB filenames are stable (mirrors DB_PATH in paper_trader / condor_sim /
# options_sim); read directly to avoid importing those modules' heavy deps.
HERE          = Path(__file__).parent
PORTFOLIO_DB  = HERE / "portfolio.db"
CONDOR_DB     = HERE / "condor.db"
OPTIONS_DB    = HERE / "options.db"


def rupees(x: float) -> str:
    return f"₹{x:,.2f}"


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else ''}{rupees(x)}"


def _connect(path: Path) -> sqlite3.Connection | None:
    """Read-only-ish open; None if the DB isn't there yet (book never run)."""
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _cash(conn: sqlite3.Connection) -> float:
    return conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()["cash"]


# ── per-book summaries (each returns a text block, or a graceful note) ─────────

def lowvol_block() -> str:
    conn = _connect(PORTFOLIO_DB)
    if conn is None:
        return "📊 Low-vol book\n  (not started yet)"
    try:
        cash = _cash(conn)
        positions = conn.execute("SELECT qty, avg_price FROM positions").fetchall()
        n = len(positions)
        # Prefer the risk governor's last MARKED equity (real, network-derived on
        # its last run); fall back to holdings-at-cost when it hasn't marked yet.
        marked = conn.execute(
            "SELECT value FROM meta WHERE key = 'governor_last'").fetchone()
        if marked:
            import json
            equity = float(json.loads(marked["value"]).get("equity") or 0.0)
            basis = "marked"
        else:
            equity = cash + sum(p["qty"] * p["avg_price"] for p in positions)
            basis = "at cost"
        realized = conn.execute(
            "SELECT COALESCE(SUM(realised_pnl), 0) r FROM fills WHERE side = 'SELL'"
        ).fetchone()["r"]
        ret = equity - PAPER_CAPITAL
        return (f"📊 Low-vol book\n"
                f"  {n} names held · cash {rupees(cash)}\n"
                f"  equity {rupees(equity)} ({basis}, {_signed(ret)}) · "
                f"realized {_signed(realized)}")
    finally:
        conn.close()


def _options_style_block(path: Path, label: str, icon: str,
                         put_col: str, call_col: str) -> str:
    """Shared summary for the two model-priced option books (same schema shape)."""
    conn = _connect(path)
    if conn is None:
        return f"{icon} {label}\n  (not started yet)"
    try:
        cash = _cash(conn)
        cyc = conn.execute(
            "SELECT * FROM cycles WHERE status = 'open'").fetchone()
        if cyc:
            last = conn.execute(
                "SELECT open_pnl FROM marks WHERE cycle_id = ? "
                "ORDER BY mark_date DESC LIMIT 1", (cyc["id"],)).fetchone()
            opnl = last["open_pnl"] if last else 0.0
            state = (f"OPEN exp {cyc['expiry']} · "
                     f"{cyc[put_col]:.0f}P/{cyc[call_col]:.0f}C · "
                     f"mark P&L {_signed(opnl)}")
        else:
            state = "FLAT (no open position)"
        agg = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(settle_pnl), 0) net "
            "FROM cycles WHERE status = 'closed'").fetchone()
        ret = cash - PAPER_CAPITAL
        return (f"{icon} {label}\n"
                f"  {state}\n"
                f"  closed {agg['n']} · realized {_signed(agg['net'])} · "
                f"equity {rupees(cash)} ({_signed(ret)})")
    finally:
        conn.close()


def condor_block() -> str:
    return _options_style_block(CONDOR_DB, "Iron condor", "🦅",
                                "sp_strike", "sc_strike")


def strangle_block() -> str:
    return _options_style_block(OPTIONS_DB, "Short strangle (retired)", "🎲",
                                "put_strike", "call_strike")


# ── compose + send ─────────────────────────────────────────────────────────────

def build_message(run_date: str | None = None) -> str:
    run_date = run_date or str(date.today())
    blocks = []
    for fn in (lowvol_block, condor_block, strangle_block):
        try:
            blocks.append(fn())
        except Exception as e:                       # one bad book never sinks the ping
            blocks.append(f"⚠ {fn.__name__.replace('_block', '')}: read failed ({e})")
    return f"📟 tradebot daily status — {run_date}\n\n" + "\n\n".join(blocks)


def main() -> int:
    msg = build_message()
    if "--dry-run" in sys.argv:
        print(msg)
        return 0
    notify_telegram.notify(msg)
    print("heartbeat sent (or no-op if Telegram not configured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
