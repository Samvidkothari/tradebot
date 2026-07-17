"""
vwap_sim.py — nightly EOD replay of the VWAP mean-reversion + Varma-sized
intraday strategy (SIMULATED book, evidence collection for the cost gate).

What one run does (once per session, after the close — run_paper_bot.sh):
  1. Fetch the latest session's REAL 15-minute candles for SYMBOL via
     market_data (throttled/retrying yfinance).
  2. Replay them through vwap_bot.VwapMeanReversionBot — the exact same code
     path as the live-shaped loop: session VWAP reset, band signals, Varma
     exposure sizing, hard stop, circuit breaker, max-hold, EOD flatten, fees.
  3. Persist to vwap.db: balance carries across days (account), every fill is
     logged (fills), and meta['last_session'] makes reruns idempotent —
     running twice in one evening simulates nothing twice.
  4. Recompute the CUMULATIVE cost-gate verdict over every fill ever logged
     and write it to results/vwap_gate.json — the pre-registered evidence
     promotion_advisor / a human needs. Default verdict is FAIL.

Why EOD replay (not a live loop): the monitoring-sandbox pattern that decided
ORB/VWAP's fate before — evidence first. Signals use closed candles only and
fills happen at candle closes, so a replay after the close produces the same
trades a live 15m loop would have, without real-time infra or data-lag noise.

SIMULATED / PAPER ONLY. Every "fill" is a local DB row. Nothing touches an
order API. Promotion to anything live requires the cost gate to PASS and the
human process in SELF_IMPROVE.md — this file only gathers the numbers.

Usage:  python vwap_sim.py            # simulate the latest closed session
        python vwap_sim.py --report   # just print the cumulative gate verdict
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import market_data
import vwap_bot
from config import PAPER_CAPITAL
from trading_calendar import TradingCalendar
from vwap_bot import Candle, VwapMeanReversionBot, SESSION_OPEN, SESSION_CLOSE

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "vwap.db"
GATE_JSON = BASE_DIR / "results" / "vwap_gate.json"
SYMBOL = vwap_bot.SYMBOL
YF_TICKER = f"{SYMBOL}.NS"
MIN_TRADES_FOR_GATE = 30          # gate verdict is provisional below this


# ── persistence ───────────────────────────────────────────────────────────────

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session TEXT, symbol TEXT, side TEXT, qty INTEGER,
            entry REAL, exit REAL, bars INTEGER, varma REAL,
            gross REAL, fees REAL, net REAL, gross_ret REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
    """)
    if conn.execute("SELECT cash FROM account WHERE id=1").fetchone() is None:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)",
                     (float(PAPER_CAPITAL),))
        conn.commit()
        print(f"Initialised VWAP sim book with ₹{PAPER_CAPITAL:,.0f} (paper)")
    return conn


def meta_get(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


# ── data: the latest closed session's 15m candles ─────────────────────────────

def fetch_session_candles() -> tuple[date, list[Candle]] | None:
    """Latest session's 15m bars from yfinance (via the throttled layer).
    Returns (session_date, candles) or None. Candle.ts is the bar CLOSE time
    (yfinance stamps bar OPEN, so shift +15m) — vwap_bot expects close-stamps."""
    df = market_data.fetch_history(YF_TICKER, interval="15m", lookback_days=5)
    if df is None or df.empty:
        return None
    last_day = df.index[-1].date()
    day = df[df.index.date == last_day]
    candles = []
    for ts, r in day.iterrows():
        close_ts = (ts + pd.Timedelta(minutes=15)).to_pydatetime()
        if not (SESSION_OPEN < close_ts.time() <= SESSION_CLOSE):
            continue
        candles.append(Candle(close_ts, float(r["open"]), float(r["high"]),
                              float(r["low"]), float(r["close"]),
                              float(r["volume"])))
    return (last_day, candles) if candles else None


# ── the cumulative cost-gate verdict (the whole point of this book) ───────────

def write_gate_report(conn) -> dict | None:
    rows = conn.execute("SELECT gross_ret FROM fills").fetchall()
    if not rows:
        return None
    import cost_gate
    g = pd.Series([r["gross_ret"] for r in rows], dtype=float)
    risk = pd.Series([vwap_bot.HARD_STOP_PCT] * len(g))
    sessions = conn.execute(
        "SELECT COUNT(DISTINCT session) AS n FROM fills").fetchone()["n"]
    tpy = (len(g) / max(sessions, 1)) * 250          # observed trades/yr pace
    res = cost_gate.evaluate(cost_gate.GateInputs(
        gross_ret=g, risk=risk, trades_per_year=tpy))
    res["n_sessions"] = sessions
    res["provisional"] = len(g) < MIN_TRADES_FOR_GATE
    res["generated"] = datetime.now().isoformat(timespec="seconds")
    res["strategy"] = f"VWAP mean-reversion 15m + Varma sizing ({SYMBOL})"
    GATE_JSON.parent.mkdir(exist_ok=True)
    GATE_JSON.write_text(json.dumps(res, indent=2, default=str))
    print(cost_gate.format_report(res, res["strategy"]))
    if res["provisional"]:
        print(f"(provisional — {len(g)}/{MIN_TRADES_FOR_GATE} trades logged; "
              f"verdict firms up as evidence accumulates)")
    return res


# ── main run ──────────────────────────────────────────────────────────────────

def main(report_only: bool = False) -> int:
    conn = db_connect()
    try:
        if report_only:
            write_gate_report(conn)
            return 0

        # Session guard (fail-open like run_paper_bot.sh's other steps).
        try:
            if not TradingCalendar().is_session(date.today()):
                print("Non-session day — VWAP sim skipped.")
                return 0
        except Exception:
            pass

        got = fetch_session_candles()
        if got is None:
            print("VWAP sim: no 15m data available — skipping (non-fatal).")
            return 0
        session, candles = got

        # Idempotency: one simulation per session, ever.
        if meta_get(conn, "last_session") == str(session):
            print(f"VWAP sim: session {session} already simulated — skipping.")
            return 0

        cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
        print(f"VWAP sim — replaying {session} ({len(candles)} × 15m candles, "
              f"book ₹{cash:,.0f})")

        bot = VwapMeanReversionBot(balance=cash)

        async def replay():
            for c in candles:
                await bot.on_candle(c)
            # Telegram alerts are queued fire-and-forget; drain them before
            # the loop closes or the nightly fill alerts would be dropped.
            await bot.notify.flush()

        asyncio.run(replay())

        # Persist: fills, balance, idempotency marker.
        for t in bot.trades:
            conn.execute(
                "INSERT INTO fills (session, symbol, side, qty, entry, exit, "
                "bars, varma, gross, fees, net, gross_ret, reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t["session"], SYMBOL, t["side"], t["qty"], t["entry"],
                 t["exit"], t["bars"], t.get("varma", 1.0), t["gross"],
                 t["fees"], t["net"], t["gross_ret"], t["reason"]))
        conn.execute("UPDATE account SET cash=? WHERE id=1", (bot.balance,))
        meta_set(conn, "last_session", str(session))
        meta_set(conn, "last_run", json.dumps(
            {"session": str(session), "n_trades": len(bot.trades),
             "balance": round(bot.balance, 2),
             "varma_factor": bot.varma_factor,
             "varma_reason": bot.varma_reason}))
        conn.commit()

        print(f"\nVWAP sim {session}: {len(bot.trades)} trade(s), "
              f"book ₹{cash:,.0f} → ₹{bot.balance:,.2f} "
              f"(varma exposure {bot.varma_factor:.0%})")
        write_gate_report(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="EOD VWAP+Varma intraday sim (paper)")
    ap.add_argument("--report", action="store_true",
                    help="print/write the cumulative cost-gate verdict only")
    args = ap.parse_args()
    raise SystemExit(main(report_only=args.report))
