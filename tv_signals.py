"""tv_signals.py — TradingView alert webhook → PAPER signals book.

TradingView has no public data API; its supported programmatic hook is the alert
WEBHOOK. A Pine Script alert POSTs a JSON payload here; we authenticate it with a
shared secret, validate it as untrusted input (strict schema + symbol allow-list
+ size caps), and SIMULATE the trade in an isolated paper account (tv.db). There
is NO order path — a hostile or malformed alert can only ever move a paper ledger.

Alert payload — put this JSON in the Pine Script alert **message**:
    {"secret":"<TV_WEBHOOK_SECRET>", "action":"BUY|SELL|CLOSE",
     "symbol":"{{ticker}}", "price":{{close}}, "weight":0.1, "strategy":"my-strat"}

Setup: set TV_WEBHOOK_SECRET in .env and point the alert webhook URL at
    http://<public-host>/api/tv/webhook
Your app must be reachable from the internet for TradingView to reach it (a tunnel
like cloudflared/ngrok, or a deploy) — localhost is not reachable from TV's servers.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import jsonify, request

import config
import data_io

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("TV_DB") or (BASE_DIR / "tv.db"))
CAPITAL = config.PAPER_CAPITAL

MAX_NAME_WEIGHT = 0.15
DEFAULT_WEIGHT = 0.10
ALLOWED_ACTIONS = {"BUY", "SELL", "CLOSE"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK (id=1), cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, qty REAL NOT NULL, avg REAL NOT NULL);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT, symbol TEXT, weight REAL,
    price REAL, strategy TEXT, accepted INTEGER, note TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS marks (
    cycle_date TEXT PRIMARY KEY, equity REAL, cash REAL, positions_value REAL, pnl REAL);
"""


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT 1 FROM account WHERE id=1").fetchone() is None:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)", (CAPITAL,))
        conn.commit()
    return conn


def universe() -> set:
    try:
        return set(data_io.close_panel().columns)
    except Exception:
        return set()


def _last_prices() -> dict:
    try:
        last = data_io.close_panel().ffill().iloc[-1]
        return {s: float(v) for s, v in last.items() if v == v}
    except Exception:
        return {}


def positions(conn) -> dict:
    return {r["symbol"]: {"qty": r["qty"], "avg": r["avg"]}
            for r in conn.execute("SELECT * FROM positions")}


def equity(conn, px: dict) -> float:
    cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
    return cash + sum(p["qty"] * px.get(s, p["avg"]) for s, p in positions(conn).items())


def _norm_symbol(sym: str) -> str:
    """Strip an exchange prefix (NSE:RELIANCE → RELIANCE) and normalise."""
    s = str(sym or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[1]
    return re.sub(r"[^A-Z0-9&_.-]", "", s)


def validate(payload: dict, uni: set) -> dict:
    """Authenticate + sanitise an alert. Returns a decision dict; accepted=False
    with a reason for anything that doesn't pass. NEVER trusts the payload."""
    secret_cfg = os.environ.get("TV_WEBHOOK_SECRET", "")
    if not secret_cfg:
        return {"accepted": False, "note": "webhook disabled — set TV_WEBHOOK_SECRET in .env"}
    if not hmac.compare_digest(str(payload.get("secret", "")), secret_cfg):
        return {"accepted": False, "note": "bad secret"}
    action = str(payload.get("action", "")).upper()
    if action not in ALLOWED_ACTIONS:
        return {"accepted": False, "note": f"bad action: {action}"}
    sym = _norm_symbol(payload.get("symbol"))
    if sym not in uni:
        return {"accepted": False, "note": f"symbol not in universe: {sym}"}
    try:
        weight = float(payload.get("weight", DEFAULT_WEIGHT))
    except Exception:
        weight = DEFAULT_WEIGHT
    weight = max(0.0, min(MAX_NAME_WEIGHT, weight))
    price = payload.get("price")
    try:
        price = float(price) if price is not None else None
    except Exception:
        price = None
    return {"accepted": True, "action": action, "symbol": sym, "weight": weight,
            "price": price, "strategy": str(payload.get("strategy", ""))[:60], "note": "ok"}


def apply(conn, d: dict, px: dict):
    """Simulate the sanitised action in the paper book (no live order)."""
    sym = d["symbol"]
    price = d["price"] or px.get(sym)
    if not price or price <= 0:
        d["note"] = "no price available"
        d["accepted"] = False
        return
    cur = positions(conn).get(sym, {"qty": 0.0, "avg": price})
    eq = equity(conn, px)
    if d["action"] in ("SELL", "CLOSE"):
        target_qty = 0.0
    else:  # BUY → target weight of equity
        target_qty = (d["weight"] * eq) / price
    trade = target_qty - cur["qty"]
    if abs(trade * price) < 1.0:
        d["note"] = "no-op (already at target)"
        return
    notional = abs(trade * price)
    cost = notional * (config.COST_ENTRY if trade > 0 else config.COST_EXIT)
    cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
    cash -= trade * price + cost
    conn.execute("UPDATE account SET cash=? WHERE id=1", (cash,))
    new_qty = cur["qty"] + trade
    if new_qty <= 1e-9:
        conn.execute("DELETE FROM positions WHERE symbol=?", (sym,))
    else:
        avg = price if cur["qty"] <= 0 else \
            (cur["avg"] * cur["qty"] + price * max(trade, 0)) / new_qty
        conn.execute("INSERT INTO positions (symbol, qty, avg) VALUES (?,?,?) "
                     "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg=excluded.avg",
                     (sym, new_qty, avg))
    conn.commit()


def handle(payload: dict) -> dict:
    conn = db_connect()
    try:
        px = _last_prices()
        d = validate(payload or {}, universe())
        if d.get("accepted"):
            apply(conn, d, px)
        today = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            "INSERT INTO signals (ts, action, symbol, weight, price, strategy, accepted, note, raw) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), d.get("action"), d.get("symbol"),
             d.get("weight"), d.get("price"), d.get("strategy"), int(bool(d.get("accepted"))),
             d.get("note"), json.dumps(payload)[:2000]))
        eq = equity(conn, px)
        posv = eq - conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
        conn.execute(
            "INSERT INTO marks (cycle_date, equity, cash, positions_value, pnl) VALUES (?,?,?,?,?) "
            "ON CONFLICT(cycle_date) DO UPDATE SET equity=excluded.equity, cash=excluded.cash, "
            "positions_value=excluded.positions_value, pnl=excluded.pnl",
            (today, eq, eq - posv, posv, eq - CAPITAL))
        conn.commit()
        return {"ok": bool(d.get("accepted")), "note": d.get("note"),
                "equity": eq, "pnl": eq - CAPITAL, "n_holdings": len(positions(conn))}
    finally:
        conn.close()


def register(app):
    """Attach the webhook route. NOT login-gated (TradingView can't authenticate
    with a session) — it is authenticated by the shared secret in the payload."""
    def webhook():
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            # TradingView may send text/plain JSON; try the raw body.
            try:
                payload = json.loads(request.get_data(as_text=True) or "{}")
            except Exception:
                payload = {}
        res = handle(payload)
        return jsonify(res), (200 if res.get("ok") else 400)

    app.add_url_rule("/api/tv/webhook", "tv_webhook", webhook, methods=["POST"])
