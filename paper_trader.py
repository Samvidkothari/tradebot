"""
paper_trader.py — Daily SIMULATED paper-trading loop (LOW-VOLATILITY strategy).

Run once per trading day, after the close. It:
  1. Decides whether today is a rebalance day (first run of a new calendar month)
  2. On a rebalance day: pulls fresh daily candles for the whole NIFTY universe
     (yfinance — no Kite token needed), ranks every stock by 60-day realized
     volatility, and targets the 15 LOWEST-vol names, equal-weight.
  3. Reconciles that target against current paper holdings — SELLs dropouts,
     trims/tops-up so every held name is ~1/15 of equity — and simulates fills.
  4. On a non-rebalance day: just marks the book to today's prices and prints.
  5. Logs the vol ranking + every fill + position to SQLite (portfolio.db).

The strategy is the one pre-registered in strategies/SPEC_lowvol.md, which
PASSED its backtest (commit 89f27b0). The signal logic lives in lowvol.py;
this file is only the "compute target -> diff against holdings -> act" adapter
described in spec §8.

THIS PLACES NO REAL ORDERS. Every "fill" is a row in a local database.
Nothing in this file touches Kite's order API. It is safe to run daily.

Running it twice in one day is harmless: the month-guard means the second run
of the month does no rebalance, and reconciliation is state-based anyway.
"""

from config import PAPER_CAPITAL
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from lowvol import target_portfolio, vol_scores, VOL_LOOKBACK, WARMUP, TOP_N
from config import COST_ENTRY, COST_EXIT   # reuse the exact cost model
import risk_governor  # automated protection: kill switch + daily-loss brake
from regime_overlay import exposure_factor  # pre-registered defensive overlay
from varma_riskstate import exposure_factor as varma_exposure_factor  # SHADOW only
import notify_telegram  # fail-soft Telegram push (no-op unless .env configured)

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR         = Path(__file__).parent / "data"
DB_PATH          = Path(__file__).parent / "portfolio.db"
STARTING_CAPITAL = PAPER_CAPITAL          # ₹10,00,000 paper money
LOOKBACK_DAYS    = 400                 # calendar days fetched — ample for 61 closes
# ─────────────────────────────────────────────────────────────────────────────


def universe():
    """The live universe = every stock CSV in data/ except the NIFTY50 index.
    yfinance ticker is the symbol + '.NS'. Returns {symbol: ticker}."""
    syms = sorted(fp.stem for fp in DATA_DIR.glob("*.csv") if fp.stem != "NIFTY50")
    return {s: f"{s}.NS" for s in syms}


# ── Money formatting (Indian style, ₹) ────────────────────────────────────────

def rupees(x):
    return f"₹{x:,.2f}"


# ── Database ──────────────────────────────────────────────────────────────────

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id   INTEGER PRIMARY KEY CHECK (id = 1),
            cash REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS positions (
            symbol    TEXT PRIMARY KEY,
            qty       INTEGER NOT NULL,
            avg_price REAL    NOT NULL,
            opened    TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date  TEXT, symbol TEXT,
            vol       REAL, rank INTEGER, in_target INTEGER
        );
        CREATE TABLE IF NOT EXISTS fills (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date   TEXT, symbol TEXT, side TEXT,
            qty        INTEGER, price REAL, cost REAL,
            cash_delta REAL, realised_pnl REAL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # First-ever run: seed the cash balance
    row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO account (id, cash) VALUES (1, ?)",
                     (STARTING_CAPITAL,))
        conn.commit()
        print(f"Initialised paper account with {rupees(STARTING_CAPITAL)}\n")
    return conn


def get_cash(conn):
    return conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()["cash"]


def set_cash(conn, amount):
    conn.execute("UPDATE account SET cash = ? WHERE id = 1", (amount,))


def get_position(conn, symbol):
    return conn.execute("SELECT * FROM positions WHERE symbol = ?",
                        (symbol,)).fetchone()


def meta_get(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


# ── Live data ─────────────────────────────────────────────────────────────────

def fetch_live(ticker):
    """Pull recent daily candles in-memory (does NOT touch the backtest CSVs)."""
    end   = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    raw = yf.Ticker(ticker).history(
        start=start, end=end + timedelta(days=1),
        interval="1d", auto_adjust=True,
    )
    if raw.empty:
        return None
    s = raw["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s.name = "close"
    return s.sort_index()


def build_panel(tickers):
    """Fetch every ticker and assemble a daily-close panel (index=dates,
    cols=symbols). Missing days become NaN — lowvol's rankability check handles
    gaps. Returns (panel, latest_close{symbol:price}, failed[symbols])."""
    closes, latest, failed = {}, {}, []
    for sym, tk in tickers.items():
        s = fetch_live(tk)
        if s is None or s.empty:
            failed.append(sym)
            continue
        closes[sym] = s
        latest[sym] = float(s.iloc[-1])
    panel = pd.DataFrame(closes).sort_index() if closes else pd.DataFrame()
    return panel, latest, failed


# ── Trade simulation (target-based, integer shares) ───────────────────────────

def simulate_sell(conn, symbol, price, run_date, sell_qty, pos):
    """Sell `sell_qty` shares of an existing position (partial or full)."""
    sell_qty = min(sell_qty, pos["qty"])
    if sell_qty < 1:
        return None
    gross    = sell_qty * price
    cost     = gross * COST_EXIT
    realised = (price - pos["avg_price"]) * sell_qty - cost

    remaining = pos["qty"] - sell_qty
    if remaining == 0:
        conn.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
    else:
        conn.execute("UPDATE positions SET qty = ? WHERE symbol = ?",
                     (remaining, symbol))      # avg_price unchanged on a sell
    set_cash(conn, get_cash(conn) + gross - cost)
    conn.execute(
        "INSERT INTO fills (run_date, symbol, side, qty, price, cost, "
        "cash_delta, realised_pnl) VALUES (?,?,?,?,?,?,?,?)",
        (run_date, symbol, "SELL", sell_qty, price, cost, gross - cost, realised),
    )
    return {"qty": sell_qty, "price": price, "cost": cost, "realised": realised}


def simulate_buy(conn, symbol, price, run_date, buy_qty):
    """Buy `buy_qty` shares, capped by available cash (incl. entry cost)."""
    cash = get_cash(conn)
    # Largest qty affordable including entry cost: q*price*(1+COST_ENTRY) <= cash
    affordable = int(cash // (price * (1 + COST_ENTRY)))
    buy_qty = min(buy_qty, affordable)
    if buy_qty < 1:
        return None
    gross = buy_qty * price
    cost  = gross * COST_ENTRY

    pos = get_position(conn, symbol)
    if pos is None:
        conn.execute(
            "INSERT INTO positions (symbol, qty, avg_price, opened) VALUES (?,?,?,?)",
            (symbol, buy_qty, price, run_date),
        )
    else:
        new_qty = pos["qty"] + buy_qty
        new_avg = (pos["avg_price"] * pos["qty"] + gross) / new_qty  # wtd avg cost
        conn.execute("UPDATE positions SET qty = ?, avg_price = ? WHERE symbol = ?",
                     (new_qty, new_avg, symbol))
    set_cash(conn, cash - gross - cost)
    conn.execute(
        "INSERT INTO fills (run_date, symbol, side, qty, price, cost, "
        "cash_delta, realised_pnl) VALUES (?,?,?,?,?,?,?,?)",
        (run_date, symbol, "BUY", buy_qty, price, cost, -(gross + cost), None),
    )
    return {"qty": buy_qty, "price": price, "cost": cost}


# ── Rebalance (the monthly decision) ──────────────────────────────────────────

def rebalance(conn, panel, latest_close, run_date, nifty_closes=None):
    """Move the book to the 15 lowest-vol names, equal-weight. SELLs run first
    (raising cash) so BUYs are funded, mirroring the backtest's turnover model.

    Regime overlay (SPEC_lowvol_regime_overlay.md): under pre-registered
    extreme stress (bear + extreme NIFTY vol) each name is sized at 0.5x and
    the rest stays in cash. Fail-safe 1.0x if NIFTY data is unavailable."""
    pos_now = len(panel.index) - 1
    scores  = vol_scores(panel, pos_now)            # ascending vol, rankable only
    if scores.empty:
        print("  Not enough history to rank — no rebalance this run.")
        return
    target  = list(scores.index[:TOP_N])

    # Log the full ranking snapshot (audit trail).
    for rank, (sym, vol) in enumerate(scores.items(), start=1):
        conn.execute(
            "INSERT INTO signals (run_date, symbol, vol, rank, in_target) "
            "VALUES (?,?,?,?,?)",
            (run_date, sym, float(vol), rank, int(sym in target)),
        )

    # Mark the book, then size each target name to total_equity / TOP_N.
    positions = {p["symbol"]: p for p in
                 conn.execute("SELECT * FROM positions").fetchall()}
    holdings_value = sum(p["qty"] * latest_close.get(s, p["avg_price"])
                         for s, p in positions.items())
    total_equity   = get_cash(conn) + holdings_value

    # Pre-registered defensive sizing overlay (never > 1.0, fail-safe 1.0).
    if nifty_closes is not None and len(nifty_closes) > 0:
        overlay = exposure_factor(nifty_closes)
    else:
        overlay = {"factor": 1.0, "stress": False,
                   "reason": "overlay inactive (no NIFTY closes this run)",
                   "regime": None}
    import json as _json
    meta_set(conn, "last_regime_overlay", _json.dumps(
        {"run_date": run_date, "factor": overlay["factor"],
         "stress": overlay["stress"], "reason": overlay["reason"],
         "tags": (overlay["regime"] or {}).get("tags", [])}))
    print(f"  Regime overlay: {overlay['reason']}")

    # ── SHADOW: Varma risk-state sizer (SPEC_varma_riskstate.md) ──────────────
    # Observation ONLY. This computes what the graded sizer WOULD have done and
    # logs it beside the live overlay for decision-by-decision comparison. It
    # NEVER affects target_each or any order — real sizing still uses `overlay`
    # above. Fail-safe: any error leaves the live path untouched.
    try:
        if nifty_closes is not None and len(nifty_closes) > 0:
            shadow = varma_exposure_factor(nifty_closes, breadth_panel=panel)
        else:
            shadow = {"factor": None, "stress": False, "risk_score": None,
                      "reason": "shadow inactive (no NIFTY closes this run)",
                      "regime": None}
        meta_set(conn, "last_varma_riskstate", _json.dumps(
            {"run_date": run_date, "factor": shadow["factor"],
             "stress": shadow["stress"], "risk_score": shadow.get("risk_score"),
             "reason": shadow["reason"], "live_factor": overlay["factor"],
             "delta": (None if shadow["factor"] is None
                       else round(shadow["factor"] - overlay["factor"], 4)),
             "tags": (shadow["regime"] or {}).get("tags", [])}))
        if shadow["factor"] is not None:
            print(f"  Varma shadow:   {shadow['reason']} "
                  f"(live {overlay['factor']:.2f} → shadow {shadow['factor']:.2f}, "
                  f"not applied)")
    except Exception as e:                              # shadow must never break a run
        print(f"  Varma shadow:   skipped ({e})")

    # ── SHADOW: governed momentum sleeve (SPEC_momentum_governed.md) ──────────
    # Observation ONLY, on the same monthly cadence. Computes what a Varma-
    # governed 12-1 momentum sleeve WOULD hold and at what exposure, and logs it.
    # This is the forward evidence the SPEC asks for before promotion. It places
    # no orders and does not touch the live low-vol book. Fail-safe on any error.
    try:
        import momentum as _mom
        if panel is not None and len(panel) > _mom.LOOKBACK:
            mday = panel.index[-1]
            mom_names = _mom.target_portfolio(panel, mday, top_n=_mom.TOP_N)
            mom_factor = (varma_exposure_factor(nifty_closes)["factor"]
                          if (nifty_closes is not None and len(nifty_closes) > 0)
                          else 0.75)
            meta_set(conn, "last_momentum_shadow", _json.dumps(
                {"run_date": run_date, "exposure_factor": mom_factor,
                 "n_names": len(mom_names), "top5": mom_names[:5],
                 "note": "governed-momentum shadow — observation only, not traded"}))
            print(f"  Momentum shadow: {len(mom_names)} names @ exposure "
                  f"{mom_factor:.2f} (not applied); top: {', '.join(mom_names[:5])}")
        else:
            meta_set(conn, "last_momentum_shadow", _json.dumps(
                {"run_date": run_date, "exposure_factor": None, "n_names": 0,
                 "top5": [], "note": "shadow inactive (insufficient history)"}))
    except Exception as e:                              # shadow must never break a run
        print(f"  Momentum shadow: skipped ({e})")

    target_each    = (total_equity * overlay["factor"]) / TOP_N

    desired = {}                                    # symbol -> desired share count
    for s in target:
        px = latest_close.get(s)
        if px and px > 0:
            desired[s] = int(target_each // px)

    print(f"  Target (15 lowest-vol): {', '.join(target)}\n")
    print(f"  {'Symbol':<12} {'Action':<22} {'Vol':>7}  Detail")
    print(f"  {'─'*70}")

    # Pass 1 — SELLs: anything held above its desired qty (dropouts -> desired 0).
    for s, p in sorted(positions.items()):
        px = latest_close.get(s)
        if px is None:                              # can't price it today; leave it
            print(f"  {s:<12} {'hold (no price)':<22} {'—':>7}")
            continue
        want = desired.get(s, 0)
        if want < p["qty"]:
            f = simulate_sell(conn, s, px, run_date, p["qty"] - want, p)
            label = f"{'SELL all' if want == 0 else 'TRIM'} {f['qty']}"
            sign = "+" if f["realised"] >= 0 else ""
            vol_d = scores.get(s, float('nan'))
            print(f"  {s:<12} {label:<22} {vol_d:>7.4f}  "
                  f"@ {rupees(px)} (realised {sign}{rupees(f['realised'])})")

    # Pass 2 — BUYs: target names below their desired qty (entries + top-ups).
    for s in target:
        px = latest_close.get(s)
        if px is None:
            print(f"  {s:<12} {'skip (no price)':<22} {scores[s]:>7.4f}")
            continue
        held = get_position(conn, s)
        have = held["qty"] if held else 0
        want = desired.get(s, 0)
        if want > have:
            f = simulate_buy(conn, s, px, run_date, want - have)
            if f:
                label = f"{'BUY' if have == 0 else 'ADD'} {f['qty']}"
                print(f"  {s:<12} {label:<22} {scores[s]:>7.4f}  @ {rupees(px)}")
            else:
                print(f"  {s:<12} {'BUY — short on cash':<22} {scores[s]:>7.4f}")

    meta_set(conn, "last_rebalance_month", run_date[:7])


# ── Main daily run ────────────────────────────────────────────────────────────

def _governor_liquidate(conn, gov, latest_close, run_date):
    """If the kill switch is ON and auto_liquidate is configured, sell every
    priceable position (SELLs are risk-reducing and always allowed). Shared by
    the hold-day path and the blocked-rebalance path so both behave the same."""
    if not (gov.get("killed") and gov.get("auto_liquidate")):
        return
    for p in conn.execute("SELECT * FROM positions").fetchall():
        px = latest_close.get(p["symbol"])
        if px:
            simulate_sell(conn, p["symbol"], px, run_date, p["qty"], p)
    print("  ⛔ Governor auto-liquidation: book moved to cash.\n")


def _equity(conn, latest_close):
    """Cash + mark-to-market holdings value (LTP, falling back to avg cost)."""
    cash = get_cash(conn)
    holdings = sum(p["qty"] * latest_close.get(p["symbol"], p["avg_price"])
                   for p in conn.execute("SELECT * FROM positions").fetchall())
    return cash + holdings


def _notify_run(conn, run_date, latest_close, headline):
    """Push one end-of-run Telegram summary: what happened today (headline),
    the fills booked, and where the book stands. Reads today's fills straight
    from the DB (each is tagged with run_date), so it stays decoupled from the
    rebalance/mark logic. Fail-soft: no-op unless .env has the Telegram keys."""
    rows = conn.execute(
        "SELECT side, COUNT(*) n FROM fills WHERE run_date = ? GROUP BY side",
        (run_date,)).fetchall()
    fills = {r["side"]: r["n"] for r in rows}
    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl), 0) r FROM fills "
        "WHERE run_date = ? AND side = 'SELL'", (run_date,)).fetchone()["r"]
    equity = _equity(conn, latest_close)
    ret = equity - STARTING_CAPITAL
    trade_line = (f"{fills.get('BUY', 0)} BUY / {fills.get('SELL', 0)} SELL"
                  if fills else "no fills")
    msg = (f"📊 Low-vol paper book — {run_date}\n"
           f"{headline}\n"
           f"Fills: {trade_line}\n"
           f"Equity {rupees(equity)} ({'+' if ret >= 0 else ''}{ret / STARTING_CAPITAL:.2%})")
    if fills.get("SELL"):
        msg += f"\nRealised today {'+' if realised >= 0 else ''}{rupees(realised)}"
    msg += "  [PAPER]"
    notify_telegram.notify(msg)


def main():
    run_date = str(date.today())
    this_month = run_date[:7]
    conn = db_connect()
    tickers = universe()

    last_month = meta_get(conn, "last_rebalance_month")
    is_rebal   = (last_month != this_month)

    print(f"Paper-trading run — {run_date}")
    print(f"Strategy: low-volatility anomaly "
          f"({VOL_LOOKBACK}d realized vol, hold {TOP_N} lowest, monthly)\n")

    headline = "Hold day (no rebalance)"   # overwritten below as the run decides
    if is_rebal:
        why = "first-ever run" if last_month is None else f"new month (last: {last_month})"
        print(f"REBALANCE DAY ({why}) — fetching {len(tickers)} stocks...\n")
        panel, latest_close, failed = build_panel(tickers)
        if failed:
            print(f"  ⚠ data fetch failed for {len(failed)}: {', '.join(failed)}\n")
        if panel.empty or len(panel) < WARMUP:
            print("  Insufficient data to rank — aborting rebalance.\n")
            headline = "⚠ Rebalance aborted — insufficient data to rank"
        elif panel.index[-1].date() != date.today():
            # Freshness guard (CODE_AUDIT_2026-07-10 §A4): today's bar is absent —
            # an NSE holiday the forward calendar can't know (Diwali, Holi, …) or
            # a stale upstream. Either way, rebalancing would fill at prices from
            # a previous session. Defer: last_rebalance_month is only written
            # inside rebalance(), so tomorrow's run retries automatically.
            print(f"  No bar for today (last: {panel.index[-1].date()}) — "
                  f"holiday or stale data; deferring rebalance to next session.\n")
            headline = "Rebalance deferred — no fresh bar (holiday/stale data)"
        else:
            # Risk governor (automated protection): evaluate limits BEFORE
            # trading; a kill-switch or daily-loss breach blocks the rebalance.
            gov = risk_governor.mark(conn, latest_close)
            allowed, why = risk_governor.allow_rebalance(gov)
            if not allowed:
                print(f"  ⛔ Rebalance BLOCKED by risk governor: {why}\n")
                # A blocked rebalance must still honour auto-liquidation —
                # otherwise a kill on a rebalance day freezes the book invested.
                _governor_liquidate(conn, gov, latest_close, run_date)
                headline = (f"⛔ Rebalance BLOCKED by risk governor: {why}"
                            + (" — book auto-liquidated to cash"
                               if gov.get("killed") and gov.get("auto_liquidate")
                               else ""))
            else:
                nifty = fetch_live("^NSEI")   # for the regime overlay (fail-safe None)
                rebalance(conn, panel, latest_close, run_date, nifty_closes=nifty)
                headline = "🔁 Monthly rebalance to 15 lowest-vol names"
    else:
        # Non-rebalance day: only need prices for held names to mark the book.
        held = [r["symbol"] for r in conn.execute("SELECT symbol FROM positions")]
        print(f"Hold day (already rebalanced for {this_month}). "
              f"Marking {len(held)} positions to market.\n")
        latest_close = {}
        for s in held:
            ser = fetch_live(tickers.get(s, f"{s}.NS"))
            if ser is not None and not ser.empty:
                latest_close[s] = float(ser.iloc[-1])
        # Governor watches hold days too — the kill switch must not wait for
        # month-end to notice a drawdown. (Optional auto-liquidation on trip.)
        gov = risk_governor.mark(conn, latest_close)
        _governor_liquidate(conn, gov, latest_close, run_date)
        if gov.get("killed") and gov.get("auto_liquidate"):
            headline = "⛔ Risk governor kill switch — book auto-liquidated to cash"

    conn.commit()
    print_portfolio(conn, latest_close)
    _notify_run(conn, run_date, latest_close, headline)
    conn.close()


def print_portfolio(conn, latest_close):
    cash = get_cash(conn)
    positions = conn.execute("SELECT * FROM positions ORDER BY symbol").fetchall()

    print(f"\n  {'─'*70}")
    print(f"  Open positions:")
    holdings_value = 0.0
    if not positions:
        print("    (none — fully in cash)")
    else:
        print(f"    {'Symbol':<12} {'Qty':>6} {'Avg':>10} {'LTP':>10} "
              f"{'Value':>14} {'Unreal P&L':>15}")
        for p in positions:
            ltp    = latest_close.get(p["symbol"], p["avg_price"])
            value  = p["qty"] * ltp
            unreal = (ltp - p["avg_price"]) * p["qty"]
            holdings_value += value
            sign = "+" if unreal >= 0 else ""
            print(f"    {p['symbol']:<12} {p['qty']:>6} {p['avg_price']:>10.2f} "
                  f"{ltp:>10.2f} {rupees(value):>14} {sign}{rupees(unreal):>14}")

    realised = conn.execute(
        "SELECT COALESCE(SUM(realised_pnl), 0) AS r FROM fills WHERE side = 'SELL'"
    ).fetchone()["r"]

    total_equity = cash + holdings_value
    total_return = total_equity - STARTING_CAPITAL

    print(f"\n  {'─'*70}")
    print(f"  Cash:            {rupees(cash):>18}")
    print(f"  Holdings value:  {rupees(holdings_value):>18}")
    print(f"  Total equity:    {rupees(total_equity):>18}")
    sign = "+" if total_return >= 0 else ""
    print(f"  Total return:    {sign}{rupees(total_return):>17}  "
          f"({sign}{total_return/STARTING_CAPITAL*100:.2f}%)")
    sign = "+" if realised >= 0 else ""
    print(f"  Realised P&L:    {sign}{rupees(realised):>17}")
    print()


if __name__ == "__main__":
    main()
