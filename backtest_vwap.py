"""
backtest_vwap.py — backtest the 15m VWAP mean-reversion strategy (vwap_bot.py)
over historical 1-MINUTE candles, with intra-bar stop resolution.

Input CSV columns (case-insensitive): Timestamp, Open, High, Low, Close, Volume.

Fidelity guarantees (the three classic backtest sins, addressed):
  RESET      VWAP/σ cumulatives are grouped per session date and cumsum'd —
             zero-based every day, no overnight carry (mirrors SessionVWAP).
  NO LOOK-AHEAD  cumsum uses only bars up to and including the current one;
             signals compare the PREVIOUS bar to the PREVIOUS bar's bands.
             `--crosscheck` replays the same 15m bars through the actual
             vwap_bot event loop and asserts the trade sequence is identical —
             the backtest cannot silently diverge from the bot.
  INTRA-BAR STOPS  within each 15m holding bar, the 1-minute Highs/Lows are
             scanned IN ORDER; a stop fills at the touch minute (at the stop,
             or that minute's open if it gapped through), matching the resting
             broker-side SL-M the bot places at entry. No end-of-candle mercy.

Frictions (per run args, defaults from the request): taker fee 0.10% per side
on notional, slippage 0.05% applied adversely to EVERY execution price.
NOTE: for NSE cash equity the repo's honest number is ~0.12%/side all-in
(cost_gate.py); 0.10%+0.05% ≈ 0.15%/side is in the same conservative zone.

Strategy constants (band K, warm-up, stop %, max hold, session times, breaker)
are IMPORTED from vwap_bot — one source of truth, nothing re-tuned here.
Sizing: fixed POSITION_FRACTION of current equity per trade (integer shares
for --market nse, fractional for crypto). The Varma overlay is intentionally
NOT applied — a generic CSV has no NIFTY context; per-trade returns and the
gate verdict are sizing-independent anyway.

RESEARCH ONLY — reads a CSV, writes metrics + a PNG. No orders, no live data.

Usage:
  python backtest_vwap.py --csv my_1min_data.csv --market nse
  python backtest_vwap.py --csv btc_1m.csv --market crypto --fee 0.001
  python backtest_vwap.py --synthetic            # self-test on generated data
  python backtest_vwap.py --synthetic --crosscheck
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

import vwap_bot
from vwap_bot import (BAND_K, HARD_STOP_PCT, DAILY_LOSS_PCT, MAX_HOLD_BARS,
                      MIN_BARS_FOR_SIG, POSITION_FRACTION, SESSION_OPEN,
                      SESSION_CLOSE, FLATTEN_AFTER)

STARTING_CAPITAL = 1_000_000.0


# ── 1. Data preparation ───────────────────────────────────────────────────────

def load_1m(csv_path: str) -> pd.DataFrame:
    """Load 1m candles: datetime index, lowercase OHLCV, sorted, deduped."""
    df = pd.read_csv(csv_path)
    cols = {c.lower().strip(): c for c in df.columns}
    need = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in cols]
    if missing:
        raise SystemExit(f"CSV missing columns: {missing} (have {list(df.columns)})")
    df = df.rename(columns={cols[c]: c for c in need})[need]
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if getattr(df["timestamp"].dt, "tz", None) is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    n0 = len(df)
    df = (df.dropna().drop_duplicates(subset="timestamp")
            .sort_values("timestamp").set_index("timestamp"))
    if len(df) < n0:
        print(f"  data hygiene: dropped {n0 - len(df)} duplicate/NaN rows")
    return df


def resample_15m(m1: pd.DataFrame, market: str) -> pd.DataFrame:
    """1m → 15m bars. Index = bar OPEN time; column bar_close = OPEN + 15m.
    For NSE, bars outside 09:15–15:30 are dropped."""
    bars = m1.resample("15min", closed="left", label="left").agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()
    bars["bar_close_ts"] = bars.index + pd.Timedelta(minutes=15)
    if market == "nse":
        t = bars["bar_close_ts"].dt.time
        bars = bars[(t > SESSION_OPEN) & (t <= SESSION_CLOSE)]
    return bars


# ── 2. Logic replication: daily-reset VWAP, σ bands, Signal column ────────────

def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Vectorized twin of vwap_bot.SessionVWAP: per-session cumsums of TP·V,
    TP²·V, V. cumsum touches only rows ≤ current bar — no look-ahead."""
    b = bars.copy()
    b["session"] = b["bar_close_ts"].dt.date
    tp = (b["high"] + b["low"] + b["close"]) / 3.0
    g = b.groupby("session")
    cum_v = g["volume"].cumsum()
    cum_pv = (tp * b["volume"]).groupby(b["session"]).cumsum()
    cum_p2v = (tp * tp * b["volume"]).groupby(b["session"]).cumsum()
    b["n_bars"] = g.cumcount() + 1
    with np.errstate(invalid="ignore", divide="ignore"):
        b["vwap"] = cum_pv / cum_v
        var = np.maximum(cum_p2v / cum_v - b["vwap"] ** 2, 0.0)
        b["sd"] = np.sqrt(var)
    b["lower"] = b["vwap"] - BAND_K * b["sd"]
    b["upper"] = b["vwap"] + BAND_K * b["sd"]
    ok = (b["n_bars"] >= MIN_BARS_FOR_SIG) & (b["sd"] > 0)
    b.loc[~ok, ["lower", "upper"]] = np.nan          # warm-up: no bands

    # Signal at bar i (evaluated at ITS close, acted on at that close):
    #   LONG : prev close < prev lower band, this close > prev close (reversal),
    #          and this close still < this vwap.  SHORT: mirror.
    prev_close = b["close"].shift(1)
    prev_lower = b["lower"].shift(1)
    prev_upper = b["upper"].shift(1)
    same_sess = b["session"].eq(b["session"].shift(1))
    long_sig = (same_sess & (prev_close < prev_lower)
                & (b["close"] > prev_close) & (b["close"] < b["vwap"]))
    short_sig = (same_sess & (prev_close > prev_upper)
                 & (b["close"] < prev_close) & (b["close"] > b["vwap"]))
    b["signal"] = np.select([long_sig, short_sig], [1, -1], default=0)
    return b


# ── 3. Realistic sequential simulation ────────────────────────────────────────

@dataclass
class Trade:
    session: str
    side: str
    qty: float
    entry_ts: pd.Timestamp
    entry: float
    exit_ts: pd.Timestamp | None = None
    exit: float | None = None
    bars: int = 0
    fees: float = 0.0
    gross: float = 0.0
    net: float = 0.0
    reason: str = ""


def simulate(bars: pd.DataFrame, m1: pd.DataFrame, fee: float, slippage: float,
             market: str) -> tuple[list[Trade], pd.Series]:
    """Sequential replay, one 15m bar at a time. Risk order mirrors the bot:
    stop (via 1m scan) → circuit breaker → max hold → EOD flatten → target."""
    equity = STARTING_CAPITAL
    trades: list[Trade] = []
    pos: Trade | None = None
    stop_px = 0.0
    day_start_eq, day_realized = equity, 0.0
    halted_for = None
    cur_session = None
    curve_idx, curve_val = [], []

    def slip(price: float, side_is_buy: bool) -> float:
        return price * (1 + slippage) if side_is_buy else price * (1 - slippage)

    def close_pos(ts, raw_px, reason):
        nonlocal pos, equity, day_realized
        px = slip(raw_px, side_is_buy=(pos.side == "SHORT"))   # exit worsens
        sign = 1 if pos.side == "LONG" else -1
        exit_fee = pos.qty * px * fee
        gross = sign * (px - pos.entry) * pos.qty
        net = gross - pos.fees - exit_fee                       # entry fee stored
        pos.exit_ts, pos.exit = ts, px
        pos.fees += exit_fee
        pos.gross, pos.net, pos.reason = gross, net, reason
        equity += net
        day_realized += net
        trades.append(pos)
        pos = None

    for open_ts, r in bars.iterrows():
        ts = r["bar_close_ts"]
        # session roll
        if r["session"] != cur_session:
            cur_session = r["session"]
            day_start_eq, day_realized = equity, 0.0
            if halted_for and halted_for != cur_session:
                halted_for = None

        if pos is not None:
            pos.bars += 1
            # 1. Stop — scan this bar's 1-MINUTE candles in time order.
            window = m1.loc[open_ts: ts - pd.Timedelta(minutes=1)]
            for mts, m in window.iterrows():
                hit = (m["low"] <= stop_px) if pos.side == "LONG" \
                      else (m["high"] >= stop_px)
                if hit:
                    raw = (min(stop_px, m["open"]) if pos.side == "LONG"
                           else max(stop_px, m["open"]))   # gap through stop
                    close_pos(mts, raw, "HARD STOP 0.5% (1m intra-bar)")
                    break
        # 2. Circuit breaker (realized, vs day-start equity).
        if halted_for is None and day_realized <= -DAILY_LOSS_PCT * day_start_eq:
            halted_for = cur_session
            if pos is not None:
                close_pos(ts, r["close"], "CIRCUIT BREAKER flatten")
        if pos is not None:
            # 3. Max holding time.
            if pos.bars >= MAX_HOLD_BARS:
                close_pos(ts, r["close"], f"MAX HOLD {MAX_HOLD_BARS} bars")
            # 4. EOD flatten (NSE MIS square-off).
            elif market == "nse" and ts.time() >= FLATTEN_AFTER:
                close_pos(ts, r["close"], "EOD FLATTEN")
            # Target: close crossed back over VWAP (bar-close event).
            elif ((pos.side == "LONG" and r["close"] >= r["vwap"]) or
                  (pos.side == "SHORT" and r["close"] <= r["vwap"])):
                close_pos(ts, r["close"], "TARGET: VWAP cross")
                r = r.copy(); r["signal"] = 0        # never flip on target bar

        # Entries (never while halted / in late window / already positioned).
        late = market == "nse" and ts.time() >= FLATTEN_AFTER
        if pos is None and not late and halted_for is None and r["signal"] != 0:
            side = "LONG" if r["signal"] == 1 else "SHORT"
            raw = r["close"]
            px = slip(raw, side_is_buy=(side == "LONG"))
            qty = (equity * POSITION_FRACTION) / px
            if market == "nse":
                qty = float(int(qty))
            if qty > 0:
                entry_fee = qty * px * fee
                pos = Trade(str(cur_session), side, qty, ts, px, fees=entry_fee)
                stop_px = px * (1 - HARD_STOP_PCT if side == "LONG"
                                else 1 + HARD_STOP_PCT)

        # equity curve mark (open position marked at bar close)
        mtm = equity
        if pos is not None:
            sign = 1 if pos.side == "LONG" else -1
            mtm += sign * (r["close"] - pos.entry) * pos.qty - pos.fees
        curve_idx.append(ts); curve_val.append(mtm)

    if pos is not None:                                   # dangling at data end
        last = bars.iloc[-1]
        close_pos(last["bar_close_ts"], last["close"], "END OF DATA flatten")
    return trades, pd.Series(curve_val, index=pd.DatetimeIndex(curve_idx))


# ── 4. Performance metrics + equity plot ──────────────────────────────────────

def report(trades: list[Trade], curve: pd.Series, fee: float, slippage: float,
           plot_path: str | None) -> dict:
    nets = np.array([t.net for t in trades])
    wins, losses = nets[nets > 0], nets[nets <= 0]
    total_ret = curve.iloc[-1] / STARTING_CAPITAL - 1 if len(curve) else 0.0
    dd = (curve / curve.cummax() - 1).min() if len(curve) else 0.0
    pf = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    m = {
        "total_return_pct": round(total_ret * 100, 3),
        "max_drawdown_pct": round(float(dd) * 100, 3),
        "win_rate_pct": round(100 * len(wins) / len(nets), 1) if len(nets) else 0.0,
        "n_trades": len(trades),
        "profit_factor": round(float(pf), 3),
        "avg_pnl_per_trade": round(float(nets.mean()), 2) if len(nets) else 0.0,
        "total_fees_and_slippage_drag": round(sum(t.fees for t in trades), 2),
    }
    print(f"\n{'─'*72}\nBACKTEST — VWAP mean-reversion 15m "
          f"(fee {fee:.3%}/side, slippage {slippage:.3%}/exec)")
    for t in trades:
        print(f"  {t.session}  {t.side:<5} qty={t.qty:<9.6g} "
              f"{t.entry:>10.2f} → {t.exit:>10.2f}  bars={t.bars} "
              f"net={t.net:>+10.2f}  {t.reason}")
    print(f"\n  Total Return   {m['total_return_pct']:>8.2f} %")
    print(f"  Max Drawdown   {m['max_drawdown_pct']:>8.2f} %")
    print(f"  Win Rate       {m['win_rate_pct']:>8.1f} %   Trades: {m['n_trades']}")
    print(f"  Profit Factor  {m['profit_factor']:>8.3f}")
    print(f"  Avg PnL/trade  {m['avg_pnl_per_trade']:>10.2f}")
    print(f"  Friction paid  {m['total_fees_and_slippage_drag']:>10.2f} (fees; "
          f"slippage is embedded in fills)")

    # Cost-gate verdict on gross per-trade returns (repo doctrine).
    try:
        import cost_gate
        g = pd.Series([(1 if t.side == "LONG" else -1)
                       * (t.exit / t.entry - 1) for t in trades])
        sessions = len({t.session for t in trades}) or 1
        res = cost_gate.evaluate(cost_gate.GateInputs(
            gross_ret=g, risk=pd.Series([HARD_STOP_PCT] * len(g)),
            trades_per_year=len(g) / sessions * 250))
        print(f"\n  COST GATE: {res['verdict']}  "
              f"(gross {res['gross_expectancy']:+.3%}/trade vs "
              f"round-trip {res['round_trip_cost']:.3%})")
        m["cost_gate"] = res["verdict"]
    except Exception:
        pass

    if plot_path and len(curve):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 5))
        ax.plot(curve.index, curve.values, lw=1.2)
        ax.axhline(STARTING_CAPITAL, ls="--", lw=0.8, alpha=0.6)
        ax.set_title("VWAP mean-reversion 15m — equity curve (net of frictions)")
        ax.set_ylabel("equity"); ax.grid(alpha=0.3)
        fig.autofmt_xdate(); fig.tight_layout()
        fig.savefig(plot_path, dpi=120)
        print(f"  equity curve -> {plot_path}")
    print("─" * 72)
    return m


# ── Cross-check: same bars through the ACTUAL bot event loop ──────────────────

def crosscheck(bars: pd.DataFrame, trades: list[Trade], fee: float) -> None:
    """Replay the identical 15m bars through vwap_bot (slippage 0 there, so
    compare a 0-slippage backtest) and assert the (session, side, reason-class)
    trade sequence matches. Guards against silent logic divergence."""
    import asyncio
    old_fee = vwap_bot.FEE_PER_SIDE
    vwap_bot.FEE_PER_SIDE = fee
    try:
        bot = vwap_bot.VwapMeanReversionBot(nifty_loader=lambda: None)
        bot.varma_factor = 1.0                    # sizing parity: no overlay
        orig_roll = bot._roll_session
        def roll(d):                               # keep factor pinned at 1.0
            orig_roll(d); bot.varma_factor = 1.0
        bot._roll_session = roll

        async def run():
            for _, r in bars.iterrows():
                c = vwap_bot.Candle(r["bar_close_ts"].to_pydatetime(),
                                    float(r["open"]), float(r["high"]),
                                    float(r["low"]), float(r["close"]),
                                    float(r["volume"]))
                await bot.on_candle(c)
        asyncio.run(run())
    finally:
        vwap_bot.FEE_PER_SIDE = old_fee

    key = lambda s, side, reason: (str(s), side, reason.split(":")[0].split("(")[0].strip())
    bt = [key(t.session, t.side, t.reason) for t in trades]
    live = [key(t["session"], t["side"], t["reason"]) for t in bot.trades]
    assert bt == live, f"DIVERGENCE!\n backtest: {bt}\n bot:      {live}"
    print(f"  CROSSCHECK OK — {len(bt)} trades identical (session, side, "
          f"exit-reason) between backtest and the live bot event loop")


# ── synthetic self-test data ──────────────────────────────────────────────────

def make_synthetic(days: int = 3, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    px, anchor = 1300.0, 1300.0
    for day in pd.bdate_range("2026-06-01", periods=days):
        ts = pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=15)
        end = pd.Timestamp(day) + pd.Timedelta(hours=15, minutes=30)
        while ts < end:
            drift = 0.03 * (anchor - px) / anchor
            c = px * (1 + drift * 0.01 + rng.normal(0, 0.0009))
            hi = max(px, c) * (1 + abs(rng.normal(0, 0.0003)))
            lo = min(px, c) * (1 - abs(rng.normal(0, 0.0003)))
            v = rng.uniform(3000, 9000) * (3 if rng.random() < 0.02 else 1)
            rows.append({"Timestamp": ts, "Open": px, "High": hi,
                         "Low": lo, "Close": c, "Volume": v})
            px = c
            ts += pd.Timedelta(minutes=1)
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--csv", help="1-minute OHLCV CSV (Timestamp,O,H,L,C,V)")
    ap.add_argument("--market", choices=["nse", "crypto"], default="nse")
    ap.add_argument("--fee", type=float, default=0.001,
                    help="taker fee per side (default 0.1%%)")
    ap.add_argument("--slippage", type=float, default=0.0005,
                    help="adverse slippage per execution (default 0.05%%)")
    ap.add_argument("--plot", default="results/vwap_backtest_equity.png")
    ap.add_argument("--synthetic", action="store_true",
                    help="self-test on generated 1m data (no CSV needed)")
    ap.add_argument("--crosscheck", action="store_true",
                    help="assert trade parity with the live vwap_bot loop")
    args = ap.parse_args()

    if args.synthetic:
        m1 = make_synthetic().set_index("Timestamp")
        m1.columns = [c.lower() for c in m1.columns]
        m1.index.name = "timestamp"
        print(f"synthetic self-test: {len(m1)} 1m bars over "
              f"{m1.index.normalize().nunique()} sessions")
    elif args.csv:
        m1 = load_1m(args.csv)
        print(f"loaded {len(m1)} 1m bars  "
              f"{m1.index[0]} → {m1.index[-1]}")
    else:
        ap.error("provide --csv PATH or --synthetic")

    bars = add_indicators(resample_15m(m1, args.market))
    print(f"resampled to {len(bars)} 15m bars, "
          f"{bars['session'].nunique()} sessions, "
          f"{int((bars['signal'] != 0).sum())} raw signals")

    trades, curve = simulate(bars, m1, args.fee, args.slippage, args.market)
    Path(args.plot).parent.mkdir(exist_ok=True)
    report(trades, curve, args.fee, args.slippage, args.plot)

    if args.crosscheck:
        t0, _ = simulate(bars, m1, args.fee, 0.0, args.market)  # slippage-free twin
        crosscheck(bars, t0, args.fee)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
