"""
views_simple.py — the plain-English home page ("/").

One screen that answers, in order, the only four questions that matter:
  1. How much money is there, and is it up or down?
  2. What did the bot do today?
  3. Is everything healthy? (run / data / backups — traffic lights)
  4. What is it holding?

Written so a nine-year-old can follow it: sentences, not jargon; one big
number; green/amber/red dots. Everything is assembled defensively — any
section that can't load degrades to a friendly placeholder, never a 500.

READ-ONLY: renders from the paper ledgers, results/ and the filesystem.
No order-placement code. The pro cockpit stays at /command ("Advanced").
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from flask import render_template

from config import PAPER_CAPITAL
from web_common import BASE_DIR, DATA_DIR, login_required, paper_db, last_close

RESULTS = BASE_DIR / "results"
BACKUPS = BASE_DIR / "backups"

# Friendly names for the strategy books (keyword → plain English).
_FRIENDLY = (
    ("low-vol",   "Steady-stocks plan (the main one)"),
    ("lowvol",    "Steady-stocks plan (the main one)"),
    ("strangle",  "Options test A (retired)"),
    ("condor",    "Options test B"),
    ("llm",       "AI stock-picker (practice)"),
    ("tv ",       "Chart-signal practice"),
    ("signals",   "Chart-signal practice"),
    ("intraday",  "Day-trading test (retired)"),
    ("momentum",  "Fast-movers test"),
)


def _friendly(name: str) -> str:
    low = (name or "").lower()
    for key, label in _FRIENDLY:
        if key in low:
            return label
    return name


def inr(x) -> str:
    """₹ with Indian digit grouping (12,34,567). None-safe."""
    if x is None:
        return "—"
    neg = x < 0
    s = f"{abs(x):,.0f}"                      # western grouping first
    parts = s.replace(",", "")
    if len(parts) > 3:                        # re-group: last 3, then pairs
        head, tail = parts[:-3], parts[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        s = ",".join(pairs + [tail])
    else:
        s = parts
    return ("-₹" if neg else "₹") + s


# ── Section builders (each one fail-soft) ─────────────────────────────────────

def _money():
    """Big number: active books' combined pretend money, and up/down."""
    try:
        import views_research as vr
        books = vr._book_rows()
    except Exception:
        books = []
    active = [b for b in books if b.get("status") == "active"]
    started = PAPER_CAPITAL * max(1, len(active))
    pnl = sum(b.get("total", 0) or 0 for b in active)
    rows = [{
        "name": _friendly(b.get("book", "?")),
        "pnl": b.get("total", 0) or 0,
        "active": b.get("status") == "active",
    } for b in books]
    rows.sort(key=lambda r: (not r["active"], -r["pnl"]))
    return {"started": started, "now": started + pnl, "pnl": pnl,
            "n_active": len(active), "books": rows}


def _today():
    """Plain sentences about what happened today."""
    today = str(date.today())
    lines, ok = [], None

    # Market open?
    try:
        from trading_calendar import TradingCalendar
        if not TradingCalendar().is_session(date.today()):
            lines.append("The stock market is closed today, so the robot is resting.")
            return {"lines": lines, "ran": None}
    except Exception:
        pass

    # Did the daily run happen, and did it work?
    try:
        rec = json.loads((RESULTS / "pipeline_run.json").read_text())
        if rec.get("generated") == today:
            failed = [s for s in rec.get("stages", []) if s.get("status") == "failed"]
            t = (rec.get("finished") or "")[11:16]
            if failed:
                ok = False
                lines.append(f"The robot did its daily check{' at ' + t if t else ''}, "
                             f"but {len(failed)} step(s) had a problem.")
            else:
                ok = True
                lines.append(f"The robot did its daily check{' at ' + t if t else ''} "
                             "and everything worked.")
        else:
            lines.append("The robot hasn't done today's check yet "
                         "(it runs after the market closes, about 3:45 pm).")
    except Exception:
        lines.append("Couldn't read today's run record.")

    # Any trades today?
    try:
        conn = paper_db()
        if conn is not None:
            fills = conn.execute(
                "SELECT side, symbol, qty FROM fills WHERE run_date = ?", (today,)
            ).fetchall()
            conn.close()
            if fills:
                for f in fills[:8]:
                    verb = "Bought" if f["side"] == "BUY" else "Sold"
                    lines.append(f"{verb} {f['qty']} shares of {f['symbol']}.")
                if len(fills) > 8:
                    lines.append(f"…and {len(fills) - 8} more trades.")
            else:
                lines.append("No buying or selling today — that's normal. "
                             "It only swaps its stocks about once a month.")
    except Exception:
        pass

    return {"lines": lines, "ran": ok}


def _light(state: str, label: str, detail: str) -> dict:
    return {"state": state, "label": label, "detail": detail}   # green/amber/red


def _health(today_ctx):
    """Three traffic lights: run, data freshness, backups."""
    lights = []

    ran = today_ctx.get("ran")
    if ran is True:
        lights.append(_light("green", "Robot ran today", "All steps finished."))
    elif ran is False:
        lights.append(_light("red", "Robot had a problem", "Check the Automation page."))
    else:
        lights.append(_light("amber", "Waiting", "Runs after market close (3:45 pm)."))

    # Price data freshness (last row of the NIFTY index file).
    try:
        last_row = None
        with open(DATA_DIR / "NIFTY50.csv", newline="") as f:
            for last_row in csv.DictReader(f):
                pass
        d = datetime.strptime(last_row["date"][:10], "%Y-%m-%d").date()
        age = (date.today() - d).days
        if age <= 3:
            lights.append(_light("green", "Prices are fresh",
                                 f"Latest prices from {d.strftime('%d %b')}."))
        else:
            lights.append(_light("red", "Prices look old",
                                 f"Newest data is {age} days old — refresh needed."))
    except Exception:
        lights.append(_light("amber", "Prices unknown", "Couldn't read the price files."))

    # Risk governor (automated protection — kill switch + daily-loss brake)
    try:
        import sqlite3 as _sq
        conn = _sq.connect(f"file:{BASE_DIR / 'portfolio.db'}?mode=ro", uri=True)
        row = conn.execute("SELECT value FROM meta WHERE key='governor_state'").fetchone()
        conn.close()
        st = json.loads(row[0]) if row else None
        if st is None or st.get("ok") is None:
            lights.append(_light("amber", "Protection warming up",
                                 "The safety guard reports after its first run."))
        elif st.get("killed"):
            lights.append(_light("red", "Protection STOPPED the robot",
                                 "It lost too much and froze itself. A human must reset it."))
        elif st.get("ok"):
            dd = st.get("drawdown")
            lights.append(_light("green", "Protection is on",
                                 f"Watching losses every day"
                                 f"{'' if dd is None else f' (currently {dd*100:+.1f}% from best)'}"  "."))
        else:
            lights.append(_light("amber", "Protection paused trading today",
                                 st.get("reason", "a daily limit was hit")))
    except Exception:
        lights.append(_light("amber", "Protection warming up",
                             "The safety guard reports after its first run."))

    # Backups
    try:
        snaps = sorted(p.name for p in BACKUPS.iterdir() if p.is_dir())
        if not snaps:
            raise FileNotFoundError
        d = datetime.strptime(snaps[-1], "%Y-%m-%d").date()
        if (date.today() - d).days <= 2:
            lights.append(_light("green", "Backups saved",
                                 f"Latest copy: {d.strftime('%d %b')}."))
        else:
            lights.append(_light("red", "Backups are old",
                                 f"Last copy was {d.strftime('%d %b')}."))
    except Exception:
        lights.append(_light("amber", "No backups yet",
                             "Turn on the backup schedule to protect the records."))
    return lights


def _holdings():
    """The stocks the main book owns, with friendly up/down vs what it paid."""
    rows = []
    try:
        conn = paper_db()
        if conn is None:
            return rows
        for p in conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall():
            px = last_close(p["symbol"]) or p["avg_price"]
            diff = (px - p["avg_price"]) * p["qty"]
            rows.append({"symbol": p["symbol"], "qty": p["qty"],
                         "pnl": diff, "up": diff >= 0})
        conn.close()
        rows.sort(key=lambda r: -r["pnl"])
    except Exception:
        pass
    return rows


# ── Route ─────────────────────────────────────────────────────────────────────

def register(app):
    def home():
        money = _money()
        today = _today()
        return render_template(
            "simple.html",
            money=money, today=today,
            lights=_health(today), holdings=_holdings(),
            inr=inr, date_str=date.today().strftime("%A, %d %B %Y"),
        )
    # Endpoint "simple_home" — "home" is already taken by the /home digest page
    # in views_research.
    app.add_url_rule("/", "simple_home", login_required(home))
