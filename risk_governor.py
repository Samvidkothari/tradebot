"""
risk_governor.py — automated pre-trade risk enforcement for the low-vol paper
book (CODE AUDIT follow-up: "Level 1 — automate safety").

Where risk_engine.py is a MONITOR (reports breaches, results/risk_engine.json),
this module is a GOVERNOR: paper_trader consults it on every run and it can
BLOCK a rebalance. It automates protection, never alpha:

  • Peak tracking       — remembers the book's all-time-high equity (meta table)
  • Kill switch (hard)  — drawdown from peak breaches max_drawdown_limit →
                          the book is FROZEN: no further rebalances until a
                          human runs `python risk_governor.py reset RESET`.
                          Holdings are NOT auto-liquidated by default (selling
                          the bottom is usually worse for a monthly book);
                          set "auto_liquidate": true in risk_limits.json to
                          change that — paper_trader honours it.
  • Daily-loss brake    — one-day loss breaches daily_loss_limit → today's
                          rebalance is skipped (soft stop; retries next run)
  • State for the UI    — writes meta['governor_state'] JSON that the simple
                          home renders as the "Protection" traffic light.

Limits come from risk_limits.json (single source, shared with the monitor).
PAPER ONLY — "blocking" means the simulator skips its simulated fills.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "portfolio.db"
LIMITS_PATH = BASE_DIR / "risk_limits.json"

K_PEAK, K_LAST, K_KILLED, K_STATE = ("governor_peak", "governor_last",
                                     "governor_killed", "governor_state")


def load_limits(path: Path = LIMITS_PATH) -> dict:
    try:
        cfg = {k: v for k, v in json.loads(Path(path).read_text()).items()
               if not k.startswith("_")}
    except Exception:
        cfg = {}
    cfg.setdefault("daily_loss_limit", -0.03)
    cfg.setdefault("max_drawdown_limit", -0.20)
    cfg.setdefault("auto_liquidate", False)
    return cfg


# ── meta helpers (self-sufficient: creates the table if missing) ──────────────

def _meta_get(conn, key):
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn, key, value):
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def book_equity(conn, latest_close: dict) -> float | None:
    """Cash + positions marked at latest_close (avg_price fallback)."""
    try:
        cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()[0]
    except Exception:
        return None
    total = float(cash)
    try:
        for r in conn.execute("SELECT symbol, qty, avg_price FROM positions"):
            total += r[1] * float(latest_close.get(r[0]) or r[2])
    except Exception:
        pass
    return total


# ── The daily evaluation (call on EVERY run — hold days included) ─────────────

def mark(conn, latest_close: dict, limits: dict | None = None,
         today: str | None = None) -> dict:
    """Update peak/last equity, evaluate the limits, trip the kill switch on a
    hard drawdown breach, persist the state for the UI, and return it."""
    L = limits or load_limits()
    today = today or str(date.today())

    eq = book_equity(conn, latest_close)
    if eq is None:
        state = {"date": today, "ok": None, "killed": False,
                 "reason": "no book yet", "equity": None}
        _meta_set(conn, K_STATE, json.dumps(state))
        return state

    peak = float(_meta_get(conn, K_PEAK) or eq)
    peak = max(peak, eq)
    _meta_set(conn, K_PEAK, str(peak))

    last_raw = _meta_get(conn, K_LAST)
    daily_ret = None
    if last_raw:
        try:
            last = json.loads(last_raw)
            if last.get("date") != today and last.get("equity"):
                daily_ret = eq / float(last["equity"]) - 1.0
        except Exception:
            pass
    _meta_set(conn, K_LAST, json.dumps({"date": today, "equity": eq}))

    drawdown = eq / peak - 1.0
    breaches = []
    if drawdown <= L["max_drawdown_limit"]:
        breaches.append(f"drawdown {drawdown:.1%} ≤ limit {L['max_drawdown_limit']:.0%}")
    daily_breach = daily_ret is not None and daily_ret <= L["daily_loss_limit"]
    if daily_breach:
        breaches.append(f"daily loss {daily_ret:.1%} ≤ limit {L['daily_loss_limit']:.0%}")

    killed_raw = _meta_get(conn, K_KILLED)
    killed = bool(killed_raw)
    if drawdown <= L["max_drawdown_limit"] and not killed:
        killed = True
        _meta_set(conn, K_KILLED, json.dumps(
            {"date": today, "reason": breaches[0], "equity": eq, "peak": peak}))
        print(f"  ⛔ RISK GOVERNOR: KILL SWITCH TRIPPED — {breaches[0]}. "
              f"Rebalances are blocked until `python risk_governor.py reset RESET`.")

    state = {"date": today, "equity": round(eq, 2), "peak": round(peak, 2),
             "drawdown": round(drawdown, 4),
             "daily_ret": None if daily_ret is None else round(daily_ret, 4),
             "breaches": breaches, "killed": killed, "daily_breach": daily_breach,
             "ok": not (killed or breaches),
             "reason": ("kill switch is ON" if killed
                        else breaches[0] if breaches else "all limits ok"),
             "auto_liquidate": bool(L.get("auto_liquidate"))}
    _meta_set(conn, K_STATE, json.dumps(state))
    conn.commit()
    return state


def allow_rebalance(state: dict) -> tuple[bool, str]:
    """Decision paper_trader asks for on a rebalance day."""
    if state.get("killed"):
        return False, "kill switch is ON (run `python risk_governor.py reset RESET`)"
    if state.get("daily_breach"):
        return False, "daily-loss brake — skipping today's rebalance"
    return True, "ok"


def reset(conn) -> bool:
    """Human action: clear the kill switch and restart peak tracking from the
    current equity (so one old peak can't instantly re-trip it)."""
    if not _meta_get(conn, K_KILLED):
        return False
    conn.execute("DELETE FROM meta WHERE key IN (?,?)", (K_KILLED, K_PEAK))
    conn.commit()
    return True


def status(conn) -> dict:
    raw = _meta_get(conn, K_STATE)
    return json.loads(raw) if raw else {"ok": None, "reason": "governor has not run yet"}


# ── CLI: python risk_governor.py status | reset RESET ────────────────────────
if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(conn), indent=2))
    elif cmd == "reset":
        if len(sys.argv) < 3 or sys.argv[2] != "RESET":
            print("This clears the kill switch. To confirm, run:\n"
                  "  python risk_governor.py reset RESET")
            sys.exit(2)
        print("Kill switch cleared." if reset(conn) else "Kill switch was not set.")
    else:
        print("usage: python risk_governor.py [status|reset RESET]")
        sys.exit(2)
