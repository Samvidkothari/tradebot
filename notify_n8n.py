"""
notify_n8n.py — post the day's run report to the n8n digest workflow.

Runs as the LAST step of run_paper_bot.sh. Collects a compact JSON summary —
run status (results/pipeline_run.json), low-vol book equity & P&L
(portfolio.db), today's fills, and the risk-governor state — and POSTs it to
the n8n webhook so n8n can email the daily digest / failure alert.

Configuration: set in .env
    N8N_RUN_WEBHOOK=https://<your-n8n>/webhook/tradebot-run-report

FAIL-SOFT AND READ-ONLY: any error (no URL configured, network down, bad DB)
prints one line and exits 0 — notification must never break the bot run.
Sends only aggregate paper-ledger numbers; no credentials, no personal data.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).parent
TIMEOUT_S = 10


def _webhook_url() -> str | None:
    try:
        from dotenv import load_dotenv
        import os
        load_dotenv(BASE_DIR / ".env")
        return os.getenv("N8N_RUN_WEBHOOK") or None
    except Exception:
        return None


def _pipeline() -> tuple[str, list]:
    """(status, failed_stage_names) from today's pipeline run record."""
    try:
        rec = json.loads((BASE_DIR / "results" / "pipeline_run.json").read_text())
        if rec.get("generated") != str(date.today()):
            return "missing", []
        failed = [s.get("name", "?") for s in rec.get("stages", [])
                  if s.get("status") == "failed"]
        return ("failed" if failed else "ok"), failed
    except Exception:
        return "unknown", []


def _last_close(symbol: str) -> float | None:
    """Most recent close from data/<symbol>.csv (cheap, no network)."""
    try:
        import csv
        last = None
        with open(BASE_DIR / "data" / f"{symbol}.csv", newline="") as f:
            for last in csv.DictReader(f):
                pass
        return float(last["close"]) if last else None
    except Exception:
        return None


def _book() -> dict:
    """Low-vol book snapshot: equity, P&L, holdings count, today's fills,
    governor state. Every field degrades to None rather than raising."""
    out = {"equity": None, "pnl_total": None, "holdings": None,
           "fills": [], "governor": {"killed": False, "state": "unknown"}}
    try:
        conn = sqlite3.connect(f"file:{BASE_DIR / 'portfolio.db'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception:
        return out
    try:
        cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()
        positions = conn.execute("SELECT * FROM positions").fetchall()
        if cash is not None:
            eq = cash["cash"]
            for p in positions:
                px = _last_close(p["symbol"]) or p["avg_price"]
                eq += p["qty"] * px
            out["equity"] = round(eq, 2)
            try:
                from config import PAPER_CAPITAL
                out["pnl_total"] = round(eq - PAPER_CAPITAL, 2)
            except Exception:
                pass
        out["holdings"] = len(positions)
        out["fills"] = [
            {"side": f["side"], "qty": f["qty"], "symbol": f["symbol"],
             "price": f["price"]}
            for f in conn.execute(
                "SELECT side, qty, symbol, price FROM fills WHERE run_date = ?",
                (str(date.today()),)).fetchall()
        ][:20]
        row = conn.execute(
            "SELECT value FROM meta WHERE key='governor_state'").fetchone()
        if row:
            st = json.loads(row["value"])
            out["governor"] = {
                "killed": bool(st.get("killed")),
                "state": st.get("reason") or (
                    "ok" if st.get("ok") else "check governor"),
            }
    except Exception:
        pass
    finally:
        conn.close()
    return out


def main() -> int:
    url = _webhook_url()
    if not url:
        print("notify_n8n: N8N_RUN_WEBHOOK not set in .env — skipping (not an error)")
        return 0

    status, failed = _pipeline()
    payload = {"date": str(date.today()), "status": status,
               "stages_failed": failed, **_book()}

    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            print(f"notify_n8n: report sent ({resp.status}) — status={status}")
    except Exception as e:
        print(f"notify_n8n: send failed ({e}) — non-fatal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
