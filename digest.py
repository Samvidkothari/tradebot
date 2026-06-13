"""
digest.py — Consolidated daily summary of every PAPER book.

Reads (read-only, no network) the three local ledgers and produces one digest:
  • Low-vol equity book   (portfolio.db)
  • Intraday ORB + VWAP    (intraday.db)
  • Options short strangle (options.db)

Used two ways:
  • CLI  — `python digest.py` prints it (the daily paper-bot run appends this).
  • Dashboard — `build_digest()` returns a dict the Home page renders.

Everything here is simulated. Nothing places an order.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

BASE     = Path(__file__).parent
CAPITAL  = 1_000_000   # per-book starting paper capital


def _conn(name):
    p = BASE / name
    if not p.exists():
        return None
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def lowvol_section():
    c = _conn("portfolio.db")
    if c is None:
        return None
    try:
        cash = _one(c, "SELECT cash FROM account WHERE id=1")
        cash = cash["cash"] if cash else CAPITAL
        npos = _one(c, "SELECT COUNT(*) n FROM positions")["n"]
        cost = _one(c, "SELECT COALESCE(SUM(qty*avg_price),0) v FROM positions")["v"]
        realised = _one(c, "SELECT COALESCE(SUM(realised_pnl),0) r "
                            "FROM fills WHERE side='SELL'")["r"]
        last = _one(c, "SELECT MAX(run_date) d FROM fills")["d"]
        return {"cash": cash, "n_positions": npos, "book_cost": cash + cost,
                "realised": realised, "last_run": last}
    finally:
        c.close()


def intraday_section():
    c = _conn("intraday.db")
    if c is None:
        return None
    try:
        strategies = [r["strategy"] for r in
                      c.execute("SELECT strategy FROM account ORDER BY strategy")]
        out = []
        latest = _one(c, "SELECT MAX(trade_date) d FROM days")["d"]
        for s in strategies:
            cash = _one(c, "SELECT cash FROM account WHERE strategy=?", (s,))["cash"]
            agg = _one(c, "SELECT COUNT(*) n, "
                          "SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) w "
                          "FROM trades WHERE strategy=?", (s,))
            today = _one(c, "SELECT n_trades, net_pnl FROM days "
                            "WHERE strategy=? AND trade_date=?", (s, latest))
            out.append({
                "strategy": s, "cum_net": cash - CAPITAL,
                "n_trades": agg["n"], "wins": agg["w"] or 0,
                "today_trades": today["n_trades"] if today else 0,
                "today_net": today["net_pnl"] if today else 0.0,
            })
        return {"latest_day": latest, "strategies": out}
    finally:
        c.close()


def options_section():
    c = _conn("options.db")
    if c is None:
        return None
    try:
        cash = _one(c, "SELECT cash FROM account WHERE id=1")
        cash = cash["cash"] if cash else CAPITAL
        cyc = _one(c, "SELECT * FROM cycles WHERE status='open'")
        op = None
        if cyc:
            mk = _one(c, "SELECT open_pnl FROM marks WHERE cycle_id=? "
                         "ORDER BY mark_date DESC LIMIT 1", (cyc["id"],))
            dte = (date.fromisoformat(cyc["expiry"]) - date.today()).days
            op = {"expiry": cyc["expiry"], "dte": dte,
                  "put_strike": cyc["put_strike"], "call_strike": cyc["call_strike"],
                  "premium_net": cyc["premium_net"],
                  "open_pnl": mk["open_pnl"] if mk else 0.0}
        n_closed = _one(c, "SELECT COUNT(*) n FROM cycles WHERE status='closed'")["n"]
        had_event = _one(c, "SELECT 1 x FROM marks WHERE ABS(daily_move)>=0.04 LIMIT 1") is not None
        return {"realised": cash - CAPITAL, "open": op,
                "n_closed": n_closed, "had_event": had_event}
    finally:
        c.close()


def build_digest():
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "lowvol": lowvol_section(),
        "intraday": intraday_section(),
        "options": options_section(),
    }


# ── CLI pretty-print ──────────────────────────────────────────────────────────

def _r(x):
    return f"₹{x:,.0f}"


def main():
    d = build_digest()
    W = 70
    print(f"\n{'='*W}\n  PAPER-BOT DAILY DIGEST — {d['generated']}   (all simulated)\n{'='*W}")

    lv = d["lowvol"]
    print("\n  Low-vol equity book")
    if lv:
        s = "+" if lv["realised"] >= 0 else ""
        print(f"    positions {lv['n_positions']}   cash {_r(lv['cash'])}   "
              f"book(cost) {_r(lv['book_cost'])}   realised {s}{_r(lv['realised'])}")
        print(f"    last activity: {lv['last_run'] or '—'}  "
              f"(live equity on the Paper Trader tab)")
    else:
        print("    (no portfolio.db yet)")

    it = d["intraday"]
    print(f"\n  Intraday  (latest day {it['latest_day'] if it else '—'})")
    if it:
        for s in it["strategies"]:
            sign = "+" if s["cum_net"] >= 0 else ""
            wr = f"{s['wins']}/{s['n_trades']}" if s["n_trades"] else "0/0"
            tsign = "+" if s["today_net"] >= 0 else ""
            print(f"    {s['strategy']:<5} cum {sign}{_r(s['cum_net'])}   "
                  f"wins {wr}   today {s['today_trades']} trades "
                  f"({tsign}{_r(s['today_net'])})")
    else:
        print("    (no intraday.db yet)")

    op = d["options"]
    print("\n  Options short strangle")
    if op:
        s = "+" if op["realised"] >= 0 else ""
        if op["open"]:
            o = op["open"]
            osign = "+" if o["open_pnl"] >= 0 else ""
            print(f"    OPEN {o['put_strike']:.0f}P/{o['call_strike']:.0f}C  "
                  f"exp {o['expiry']} ({o['dte']}d)  premium {_r(o['premium_net'])}  "
                  f"mark {osign}{_r(o['open_pnl'])}")
        else:
            print("    flat (no open position)")
        print(f"    realised {s}{_r(op['realised'])}   closed cycles {op['n_closed']}   "
              f"{'vol event seen' if op['had_event'] else 'VERDICT: inconclusive (awaiting vol event)'}")
    else:
        print("    (no options.db yet)")
    print(f"\n{'='*W}\n")


if __name__ == "__main__":
    main()
