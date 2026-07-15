"""ceinsys_alert.py — fire an n8n alert when CEINSYS reclaims its 200-DMA.

Per strategies/SPEC_ceinsys_swing.md the trade is a WATCH-LIST item while price is
below its 200-day average (the current regime). This watcher checks the cached
daily data once per run and POSTs to the same n8n webhook notify_n8n.py uses, but
ONLY on the day price freshly closes back above the 200-DMA — the moment the
strategy's trend gate first opens. On that day the payload also carries the ready
-to-use plan (entry / 2×ATR stop / +20% target / 1%-risk size) so the email is
actionable.

Edge-triggered on purpose: it compares the last two bars (yesterday below, today
above) so it fires once at the crossover, not every day the stock sits above the
line. Use --force to send the current state regardless (for testing the webhook).

FAIL-SOFT AND READ-ONLY: any problem (missing data, no URL, network down) prints
one line and exits 0 — an alert must never break the bot run. Places no orders.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

import config
from notify_n8n import _webhook_url, TIMEOUT_S
from trailing_exit import atr as _atr

BASE_DIR = Path(__file__).parent
SYMBOL = "CEINSYS"
TREND_MA = 200                      # matches SPEC ceinsys_swing
ATR_STOP_MULT = 2.0                 # matches SPEC
TARGET_RET = 0.20                   # matches SPEC
RISK_PER_TRADE = 0.01               # matches SPEC


def _load() -> pd.DataFrame | None:
    fp = BASE_DIR / "data" / f"{SYMBOL}.csv"
    if not fp.exists():
        print(f"ceinsys_alert: data/{SYMBOL}.csv not found — run fetch_ceinsys.py "
              "(skipping, not an error)")
        return None
    try:
        return (pd.read_csv(fp, parse_dates=["date"])
                .sort_values("date").reset_index(drop=True))
    except Exception as e:                                   # noqa: BLE001
        print(f"ceinsys_alert: could not read data ({e}) — skipping")
        return None


def evaluate(df: pd.DataFrame) -> dict:
    """Trend-gate state + (if a fresh cross-up) the actionable plan. Pure logic."""
    close = df["close"]
    sma = close.rolling(TREND_MA).mean()
    if sma.notna().sum() < 2:
        return {"enough_history": False}

    today_c, prev_c = float(close.iloc[-1]), float(close.iloc[-2])
    today_ma, prev_ma = float(sma.iloc[-1]), float(sma.iloc[-2])
    above = today_c > today_ma
    cross_up = (prev_c <= prev_ma) and above

    a = float(_atr(df["high"], df["low"], df["close"], 14)[-1])
    entry = today_c
    stop = entry - ATR_STOP_MULT * a
    risk_ps = entry - stop
    qty = int((config.PAPER_CAPITAL * RISK_PER_TRADE) // risk_ps) if risk_ps > 0 else 0

    return {
        "enough_history": True,
        "date": str(df["date"].iloc[-1].date()),
        "close": round(today_c, 2),
        "sma200": round(today_ma, 2),
        "pct_vs_sma200": round((today_c / today_ma - 1) * 100, 2),
        "state": "above" if above else "below",
        "cross_up": cross_up,
        "plan": {
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(entry * (1 + TARGET_RET), 2),
            "atr14": round(a, 2),
            "qty_at_1pct_risk": qty,
            "capital": config.PAPER_CAPITAL,
        },
    }


def _post(url: str, payload: dict) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            print(f"ceinsys_alert: alert sent ({resp.status})")
    except Exception as e:                                   # noqa: BLE001
        print(f"ceinsys_alert: send failed ({e}) — non-fatal")


def main(force: bool = False) -> int:
    df = _load()
    if df is None:
        return 0
    ev = evaluate(df)
    if not ev.get("enough_history"):
        print("ceinsys_alert: <200 bars of history yet — skipping")
        return 0

    tag = "CROSS-UP (actionable)" if ev["cross_up"] else f"state={ev['state']}"
    print(f"ceinsys_alert: {SYMBOL} {ev['close']} vs 200-DMA {ev['sma200']} "
          f"({ev['pct_vs_sma200']:+.1f}%) — {tag}")

    if not (ev["cross_up"] or force):
        return 0                                            # nothing to shout about

    url = _webhook_url()
    if not url:
        print("ceinsys_alert: N8N_RUN_WEBHOOK not set in .env — skipping send")
        return 0

    payload = {
        "type": "ceinsys_trend_alert",
        "date": str(date.today()),
        "headline": (f"CEINSYS reclaimed its 200-DMA — trend gate OPEN "
                     f"(₹{ev['close']} > ₹{ev['sma200']})") if ev["cross_up"]
        else (f"CEINSYS status: {ev['state']} 200-DMA "
              f"(₹{ev['close']} vs ₹{ev['sma200']})"),
        "actionable": ev["cross_up"],
        **ev,
        "note": ("Target, not a guarantee. Paper/research only — no order is placed. "
                 "Confirm a clean price-action entry before acting."),
    }
    _post(url, payload)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="send the current state even without a fresh cross-up")
    args = ap.parse_args()
    sys.exit(main(force=args.force))
