"""llm_analyst.py — LLM "expert trader" PAPER book (JSON decisions, scored).

Inspired by the LLM-trading demos: feed a large language model the same indicators
the rest of the system already computes, plus the live paper portfolio, ask for a
JSON trade decision, and SIMULATE it in an isolated paper account so it can be
scored head-to-head against the pre-registered rule-based strategies.

SAFETY — this places NO live orders and adds no order path. The model's output is
untrusted DATA: it is parsed into a strict schema, every symbol must be in the
loaded NIFTY universe, per-name and gross weights are capped, and anything that
fails validation is dropped (the book simply holds). The only side effect is
writing the isolated paper ledger llm.db. The LLM call is pluggable: OpenRouter
when OPENROUTER_API_KEY is set (Claude / Deepseek / Qwen, like the video), else a
deterministic offline fallback so the book runs and is fully testable without a
key or network.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import config
import data_io

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
DB_PATH = Path(os.environ.get("LLM_DB") or (BASE_DIR / "llm.db"))
CAPITAL = config.PAPER_CAPITAL

# ── Hard risk rails on whatever the model returns (untrusted output) ───────────
MAX_NAME_WEIGHT = 0.15     # <=15% of equity in any one name
MAX_GROSS       = 0.95     # <=95% invested (>=5% cash)
MAX_NAMES       = 10       # at most 10 holdings
ALLOWED_ACTIONS = {"BUY", "SELL", "HOLD"}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")

import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK (id=1), cash REAL NOT NULL);
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, qty REAL NOT NULL, avg REAL NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, cycle_date TEXT, model TEXT,
    raw TEXT, parsed TEXT, confidence REAL, rationale TEXT, accepted INTEGER, note TEXT);
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


# ── Market snapshot (the same indicators the rest of the system computes) ─────

def _load_json(name):
    fp = RESULTS_DIR / name
    try:
        return json.loads(fp.read_text())
    except Exception:
        return {}


def universe() -> set:
    try:
        return set(data_io.close_panel().columns)
    except Exception:
        return set()


def prices() -> dict:
    """Latest cached close per symbol (offline)."""
    try:
        cp = data_io.close_panel()
        last = cp.ffill().iloc[-1]
        return {s: float(v) for s, v in last.items() if v == v}
    except Exception:
        return {}


def positions(conn) -> dict:
    return {r["symbol"]: {"qty": r["qty"], "avg": r["avg"]}
            for r in conn.execute("SELECT * FROM positions")}


def build_snapshot(conn) -> dict:
    mf = _load_json("multifactor.json")
    ts = _load_json("tearsheets.json")
    px = prices()
    pos = positions(conn)
    cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
    eq = equity(conn, px)
    holdings = [{"symbol": s, "qty": round(p["qty"], 2),
                 "value": round(p["qty"] * px.get(s, p["avg"]), 0)} for s, p in pos.items()]
    return {
        "as_of": mf.get("as_of") or ts.get("generated"),
        "regime": (ts.get("regime") or {}).get("tags"),
        "regime_reason": (ts.get("regime") or {}).get("reason"),
        "factor_leaderboard": (mf.get("top") or [])[:15],
        "portfolio": {"cash": round(cash, 0), "equity": round(eq, 0), "holdings": holdings},
        "rails": {"max_name_weight": MAX_NAME_WEIGHT, "max_gross": MAX_GROSS,
                  "max_names": MAX_NAMES},
    }


# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM = (
    "You are a disciplined systematic equity trader managing a NIFTY-50 paper "
    "portfolio. You are given precomputed factor scores, the market regime, and "
    "your current holdings. Decide a target book. Respect the risk rails. Reply "
    "with ONLY a JSON object, no prose, of the form: "
    '{"decisions":[{"action":"BUY","symbol":"INFY","weight":0.1}], '
    '"confidence":0.0-1.0, "rationale":"one sentence"}. '
    "weight is the target fraction of equity for that name (0..max_name_weight). "
    "Use SELL to exit a held name, HOLD to keep the book unchanged. Only use "
    "symbols present in the factor leaderboard or your current holdings."
)


def build_prompt(snapshot: dict) -> list:
    user = "MARKET SNAPSHOT\n" + json.dumps(snapshot, indent=1)
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": user}]


# ── The pluggable decider ─────────────────────────────────────────────────────

def call_llm(messages: list) -> tuple:
    """Return (raw_text, model). Uses OpenRouter if OPENROUTER_API_KEY is set,
    else the offline fallback. Any error falls back — the book must never crash on
    a flaky API."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        try:
            import requests
            r = requests.post(OPENROUTER_URL, timeout=30,
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                              json={"model": MODEL, "messages": messages,
                                    "temperature": 0.2})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"], MODEL
        except Exception as e:
            return _fallback(messages), f"fallback ({type(e).__name__})"
    return _fallback(messages), "fallback (no key)"


def _fallback(messages: list) -> str:
    """Deterministic baseline decider: equal-weight the top factor names up to the
    rails. Lets the harness run and be scored without an LLM — and is a fair,
    non-LLM benchmark to compare any model against."""
    snap = {}
    for m in messages:
        if m["role"] == "user":
            try:
                snap = json.loads(m["content"].split("\n", 1)[1])
            except Exception:
                snap = {}
    top = [d["symbol"] for d in snap.get("factor_leaderboard", [])[:6]]
    w = round(min(MAX_NAME_WEIGHT, MAX_GROSS / max(1, len(top))), 4)
    return json.dumps({"decisions": [{"action": "BUY", "symbol": s, "weight": w} for s in top],
                       "confidence": 0.5, "rationale": "equal-weight top factor names (fallback)"})


# ── Parse + validate (untrusted output → strict, capped schema) ───────────────

def parse_decision(text: str, uni: set) -> dict:
    """Extract and sanitise the model's JSON. Returns
    {target: {symbol: weight}, confidence, rationale, accepted, note, dropped}.
    Never trusts the output: unknown symbols dropped, weights capped, gross scaled,
    count capped. Unparseable → HOLD (no trade)."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {"target": None, "confidence": 0.0, "rationale": "", "accepted": False,
                "note": "no JSON in output", "dropped": []}
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return {"target": None, "confidence": 0.0, "rationale": "", "accepted": False,
                "note": "unparseable JSON", "dropped": []}

    target, dropped = {}, []
    for d in (obj.get("decisions") or []):
        if not isinstance(d, dict):
            dropped.append(str(d)[:40]); continue
        action = str(d.get("action", "")).upper()
        sym = str(d.get("symbol", "")).strip().upper()
        if action not in ALLOWED_ACTIONS:
            dropped.append(f"{sym}:bad-action"); continue
        if action in ("BUY", "SELL") and sym not in uni:
            dropped.append(f"{sym}:not-in-universe"); continue
        if action == "SELL":
            target[sym] = 0.0
        elif action == "BUY":
            try:
                w = float(d.get("weight", 0))
            except Exception:
                w = 0.0
            target[sym] = max(0.0, min(MAX_NAME_WEIGHT, w))   # clamp per-name
        # HOLD → no entry (handled as "keep current" at apply time)
    # cap number of names (keep largest weights)
    buys = {s: w for s, w in target.items() if w > 0}
    if len(buys) > MAX_NAMES:
        keep = dict(sorted(buys.items(), key=lambda kv: kv[1], reverse=True)[:MAX_NAMES])
        for s in list(buys):
            if s not in keep:
                target[s] = 0.0
        buys = keep
    # scale gross exposure down to the cap
    gross = sum(buys.values())
    if gross > MAX_GROSS and gross > 0:
        scale = MAX_GROSS / gross
        for s in buys:
            target[s] = round(target[s] * scale, 4)
    conf = obj.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except Exception:
        conf = 0.0
    return {"target": target, "confidence": conf,
            "rationale": str(obj.get("rationale", ""))[:200],
            "accepted": True, "note": "ok", "dropped": dropped}


# ── Paper execution (simulated — NEVER a live order) ──────────────────────────

def equity(conn, px: dict) -> float:
    cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
    val = sum(p["qty"] * px.get(s, p["avg"]) for s, p in positions(conn).items())
    return cash + val


def apply(conn, parsed: dict, px: dict):
    """Move the paper book toward the sanitised target weights, at cached prices,
    paying config costs. HOLD-only / rejected decisions leave the book unchanged."""
    target = parsed.get("target")
    if not target:
        return
    eq = equity(conn, px)
    cur = positions(conn)
    # Build the full target book: named targets, everything else → 0 unless kept.
    # Any currently-held name not mentioned is left as-is (HOLD semantics), so an
    # explicit weight of 0.0 (SELL) is required to exit.
    for sym, w in target.items():
        price = px.get(sym)
        if not price or price <= 0:
            continue
        target_qty = (w * eq) / price
        cur_qty = cur.get(sym, {}).get("qty", 0.0)
        trade = target_qty - cur_qty
        if abs(trade * price) < 1.0:
            continue
        notional = abs(trade * price)
        cost = notional * (config.COST_ENTRY if trade > 0 else config.COST_EXIT)
        cash = conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
        cash -= trade * price + cost
        conn.execute("UPDATE account SET cash=? WHERE id=1", (cash,))
        new_qty = cur_qty + trade
        if new_qty <= 1e-9:
            conn.execute("DELETE FROM positions WHERE symbol=?", (sym,))
        else:
            avg = price if cur_qty <= 0 else \
                (cur.get(sym, {}).get("avg", price) * cur_qty + price * max(trade, 0)) / new_qty
            conn.execute(
                "INSERT INTO positions (symbol, qty, avg) VALUES (?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET qty=excluded.qty, avg=excluded.avg",
                (sym, new_qty, avg))
    conn.commit()


def run_cycle(conn) -> dict:
    px = prices()
    snap = build_snapshot(conn)
    raw, model = call_llm(build_prompt(snap))
    parsed = parse_decision(raw, universe())
    apply(conn, parsed, px)
    today = (snap.get("as_of") or datetime.now().strftime("%Y-%m-%d"))
    conn.execute(
        "INSERT INTO decisions (ts, cycle_date, model, raw, parsed, confidence, "
        "rationale, accepted, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), today, model, raw[:4000],
         json.dumps(parsed.get("target")), parsed["confidence"], parsed["rationale"],
         int(parsed["accepted"]), parsed["note"]))
    eq = equity(conn, px)
    posv = eq - conn.execute("SELECT cash FROM account WHERE id=1").fetchone()["cash"]
    conn.execute(
        "INSERT INTO marks (cycle_date, equity, cash, positions_value, pnl) VALUES (?,?,?,?,?) "
        "ON CONFLICT(cycle_date) DO UPDATE SET equity=excluded.equity, cash=excluded.cash, "
        "positions_value=excluded.positions_value, pnl=excluded.pnl",
        (today, eq, eq - posv, posv, eq - CAPITAL))
    conn.commit()
    return {"model": model, "accepted": parsed["accepted"], "note": parsed["note"],
            "confidence": parsed["confidence"], "dropped": parsed["dropped"],
            "n_holdings": len(positions(conn)), "equity": eq, "pnl": eq - CAPITAL}


def report(conn):
    px = prices()
    eq = equity(conn, px)
    print(f"\n{'='*70}\n  LLM ANALYST — paper book (simulated · no live orders)\n{'='*70}")
    print(f"  Equity {eq:,.0f}   P&L {eq-CAPITAL:+,.0f}   (started {CAPITAL:,.0f})")
    last = conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
    if last:
        print(f"  Last decision [{last['model']}] conf {last['confidence']:.2f} · "
              f"{'accepted' if last['accepted'] else 'REJECTED'} ({last['note']})")
        print(f"    rationale: {last['rationale']}")
    for r in conn.execute("SELECT * FROM positions ORDER BY qty*avg DESC"):
        print(f"    {r['symbol']:14s} qty {r['qty']:.1f} @ {r['avg']:.1f}")
    print(f"{'='*70}\n")


def main():
    conn = db_connect()
    print("LLM analyst paper book — one cycle")
    s = run_cycle(conn)
    print(f"  model={s['model']} accepted={s['accepted']} holdings={s['n_holdings']} "
          f"equity={s['equity']:,.0f}")
    report(conn)
    conn.close()


if __name__ == "__main__":
    main()
