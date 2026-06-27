"""
backtest.py — SMA 20/50 crossover backtest on 10 NIFTY 50 stocks.

Run after fetch_data.py has populated data/.

Output:
  • terminal summary table
  • results/report.md
"""

from datetime import date
from pathlib import Path

import pandas as pd

from strategy import generate_signals, FAST_PERIOD, SLOW_PERIOD, TREND_PERIOD
# Cost model + OOS split now live in config.py (single source of truth);
# re-exported here so existing `from backtest import COST_*` importers still work.
from config import (COST_ENTRY, COST_EXIT, COST_ROUNDTRIP,  # noqa: F401
                    SPLIT_DATE)

SYMBOLS     = ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK",
               "LT", "SBIN", "BHARTIARTL", "ITC", "HINDUNILVR"]
DATA_DIR    = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"


# ── Backtest engine ───────────────────────────────────────────────────────────

def run_backtest(df):
    """
    Simulate the SMA crossover strategy on df.

    Execution model (close-to-close with next-bar signal):
      • Signal computed from close[t]
      • Position and costs applied starting close[t] → close[t+1]
      • i.e. pos_prev drives today's return — standard daily-bar convention

    Returns
      equity  : pd.Series (date index), starts at 1.0
      trades  : pd.DataFrame with columns entry, exit, return, days
    """
    df = generate_signals(df).copy()
    df = df.set_index("date").sort_index()

    df["ret"]      = df["close"].pct_change()
    df["pos_prev"] = df["position"].shift(1).fillna(0)
    df["strat_ret"] = df["pos_prev"] * df["ret"]

    # Subtract transaction costs on days position changes
    entries = (df["position"] == 1) & (df["pos_prev"] == 0)
    exits   = (df["position"] == 0) & (df["pos_prev"] == 1)
    df.loc[entries, "strat_ret"] -= COST_ENTRY
    df.loc[exits,   "strat_ret"] -= COST_EXIT

    df = df.dropna(subset=["strat_ret"])
    df["equity"] = (1 + df["strat_ret"]).cumprod()

    # Recompute on the trimmed df so shapes match
    entries = (df["position"] == 1) & (df["pos_prev"] == 0)
    exits   = (df["position"] == 0) & (df["pos_prev"] == 1)

    entry_dates = df.index[entries].tolist()
    exit_dates  = df.index[exits].tolist()

    # If still in a trade at period end, close it at the last bar
    if len(entry_dates) > len(exit_dates):
        exit_dates.append(df.index[-1])

    trades = []
    for ent, ex in zip(entry_dates, exit_dates):
        eq_entry = df.loc[ent, "equity"]
        eq_exit  = df.loc[ex,  "equity"]
        trades.append({
            "entry":  ent,
            "exit":   ex,
            "return": float(eq_exit / eq_entry) - 1.0,
            "days":   (ex - ent).days,
        })

    trades_df = (pd.DataFrame(trades)
                 if trades
                 else pd.DataFrame(columns=["entry", "exit", "return", "days"]))
    return df["equity"], trades_df


def compute_metrics(equity, trades):
    """Performance metrics from an equity curve + trade list."""
    if equity.empty or len(equity) < 2:
        return None

    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return None

    total_ret = float(equity.iloc[-1] - 1)
    cagr      = float(equity.iloc[-1] ** (1 / years) - 1)

    rolling_max = equity.cummax()
    drawdown    = (equity - rolling_max) / rolling_max
    max_dd      = float(drawdown.min())

    # Longest consecutive days underwater
    max_dd_days = cur = 0
    for under in (drawdown < 0):
        cur = cur + 1 if under else 0
        max_dd_days = max(max_dd_days, cur)

    n = len(trades)
    if n > 0:
        rets   = trades["return"]
        wins   = rets[rets > 0]
        losses = rets[rets <= 0]
        win_rate = float(len(wins) / n)
        avg_win  = float(wins.mean())  if len(wins)   else 0.0
        avg_loss = float(losses.mean()) if len(losses) else 0.0

        streak = losing_streak = 0
        for r in rets:
            streak = streak + 1 if r <= 0 else 0
            losing_streak = max(losing_streak, streak)
    else:
        win_rate = avg_win = avg_loss = 0.0
        losing_streak = 0

    return dict(total_ret=total_ret, cagr=cagr, max_dd=max_dd,
                max_dd_days=max_dd_days, n_trades=n, win_rate=win_rate,
                avg_win=avg_win, avg_loss=avg_loss,
                losing_streak=losing_streak, years=round(years, 1))


def slice_metrics(equity, trades, start=None, end=None):
    """Metrics for a date slice — normalises equity to 1.0 at period start."""
    eq = equity.copy()
    if start:
        eq = eq[eq.index >= pd.Timestamp(start)]
    if end:
        eq = eq[eq.index <  pd.Timestamp(end)]
    if eq.empty:
        return None
    eq = eq / eq.iloc[0]

    tr = trades.copy()
    if start:
        tr = tr[tr["entry"] >= pd.Timestamp(start)]
    if end:
        tr = tr[tr["entry"] <  pd.Timestamp(end)]

    return compute_metrics(eq, tr)


def bnh_metrics(nifty_df, start=None, end=None):
    """Buy-and-hold metrics for the NIFTY 50 price series."""
    d = nifty_df.copy()
    if start:
        d = d[d["date"] >= pd.Timestamp(start)]
    if end:
        d = d[d["date"] <  pd.Timestamp(end)]
    if len(d) < 2:
        return None
    ret   = d["close"].pct_change().dropna()
    eq    = (1 + ret).cumprod()
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    rmax  = eq.cummax()
    return dict(
        total_ret = float(eq.iloc[-1] - 1),
        cagr      = float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0,
        max_dd    = float(((eq - rmax) / rmax).min()),
        years     = round(years, 1),
    )


# ── Portfolio aggregation ─────────────────────────────────────────────────────

def portfolio_metrics(per_stock):
    """Equal-weight average of per-stock metrics."""
    vals = [m for m in per_stock.values() if m]
    if not vals:
        return None
    return dict(
        total_ret     = sum(m["total_ret"]     for m in vals) / len(vals),
        cagr          = sum(m["cagr"]          for m in vals) / len(vals),
        max_dd        = min(m["max_dd"]        for m in vals),
        max_dd_days   = max(m["max_dd_days"]   for m in vals),
        n_trades      = sum(m["n_trades"]      for m in vals),
        win_rate      = sum(m["win_rate"]      for m in vals) / len(vals),
        avg_win       = sum(m["avg_win"]       for m in vals) / len(vals),
        avg_loss      = sum(m["avg_loss"]      for m in vals) / len(vals),
        losing_streak = max(m["losing_streak"] for m in vals),
        years         = sum(m["years"]         for m in vals) / len(vals),
    )


# ── Formatting helpers ────────────────────────────────────────────────────────

def _p(v, decimals=1):
    return f"{v*100:+.{decimals}f}%" if v is not None else "  n/a"

def _pp(v, decimals=1):
    return f"{v*100:.{decimals}f}%" if v is not None else "n/a"


# ── Terminal output ───────────────────────────────────────────────────────────

def print_section(title, per_stock, port, nifty):
    W = 115
    print(f"\n{'─'*W}")
    print(f"  {title}")
    print(f"{'─'*W}")
    hdr = (f"  {'Symbol':<16} {'CAGR':>7} {'Total':>7} {'MaxDD':>7} "
           f"{'Trades':>7} {'WinRate':>8} {'AvgWin':>8} {'AvgLoss':>8} {'LosStr':>7}")
    print(hdr)
    print(f"  {'─'*109}")

    for sym, m in per_stock.items():
        if not m:
            print(f"  {sym:<16}  (insufficient data)")
            continue
        print(f"  {sym:<16} {_p(m['cagr']):>7} {_p(m['total_ret']):>7} {_p(m['max_dd']):>7} "
              f"{m['n_trades']:>7} {_pp(m['win_rate']):>8} "
              f"{_p(m['avg_win']):>8} {_p(m['avg_loss']):>8} {m['losing_streak']:>7}")

    if port:
        print(f"  {'─'*109}")
        print(f"  {'PORTFOLIO (eq-wt)':<16} {_p(port['cagr']):>7} {_p(port['total_ret']):>7} "
              f"{_p(port['max_dd']):>7} {port['n_trades']:>7} {_pp(port['win_rate']):>8} "
              f"{_p(port['avg_win']):>8} {_p(port['avg_loss']):>8} {port['losing_streak']:>7}")

    if nifty:
        print(f"\n  NIFTY 50 buy-and-hold:  CAGR {_p(nifty['cagr'])}  "
              f"Total {_p(nifty['total_ret'])}  Max DD {_p(nifty['max_dd'])}")


# ── Markdown report ───────────────────────────────────────────────────────────

def _md_row(sym, m):
    if not m:
        return f"| {sym} | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |\n"
    return (f"| {sym} | {_p(m['cagr'])} | {_p(m['total_ret'])} | {_p(m['max_dd'])} "
            f"| {m['n_trades']} | {_pp(m['win_rate'])} "
            f"| {_p(m['avg_win'])} | {_p(m['avg_loss'])} | {m['losing_streak']} |\n")


def _md_section(title, per_stock, port, nifty):
    lines = [f"\n## {title}\n\n"]
    lines.append("| Symbol | CAGR | Total | MaxDD | Trades | WinRate | AvgWin | AvgLoss | LosStr |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")
    for sym, m in per_stock.items():
        lines.append(_md_row(sym, m))
    if port:
        lines.append(_md_row("**PORTFOLIO (eq-wt)**", port))
    if nifty:
        lines.append(f"\n**NIFTY 50 buy & hold:** CAGR {_p(nifty['cagr'])}  "
                     f"Total {_p(nifty['total_ret'])}  Max DD {_p(nifty['max_dd'])}\n")
    return lines


def save_report(symbols, full, period_a, period_b, nifty_full, nifty_a, nifty_b):
    RESULTS_DIR.mkdir(exist_ok=True)

    port_full = portfolio_metrics(full)
    port_a    = portfolio_metrics(period_a)
    port_b    = portfolio_metrics(period_b)

    n = len(symbols)
    if symbols == SYMBOLS:
        universe = f"{n} NIFTY 50 large-caps (full default basket)"
    else:
        universe = (f"{n}-stock SUBSET — {', '.join(symbols)}.  "
                    f"**Caution:** a hand-picked subset is not a fair test; "
                    f"if these were chosen after seeing results, the numbers below "
                    f"reflect selection bias, not a real edge")

    trend_title = f" + {TREND_PERIOD}d Trend Filter" if TREND_PERIOD else ""
    trend_desc  = (f" — but only while the close is above its {TREND_PERIOD}-day SMA "
                   f"(long-term trend filter)" if TREND_PERIOD else "")

    lines = []
    lines.append(f"# SMA {FAST_PERIOD}/{SLOW_PERIOD} Crossover{trend_title} — Backtest Report\n\n")
    lines.append(f"Generated: {date.today()}  \n")
    lines.append(f"Strategy: buy when {FAST_PERIOD}-day SMA crosses above {SLOW_PERIOD}-day SMA{trend_desc}; "
                 f"exit when it crosses back below.  \n")
    lines.append(f"Universe: {universe}, equal-weight portfolio.  \n")
    lines.append(f"Data: yfinance NSE daily OHLCV (auto-adjusted).  \n")

    lines.append("\n## Cost Model (Zerodha Equity Delivery)\n\n")
    lines.append("| Item | Rate |\n|---|---|\n")
    lines.append(f"| Brokerage | ₹0 (delivery) |\n")
    lines.append(f"| STT | 0.10% buy + 0.10% sell |\n")
    lines.append(f"| Exchange (NSE) | 0.00345% each side |\n")
    lines.append(f"| SEBI | 0.0001% each side |\n")
    lines.append(f"| Stamp duty | 0.015% on buy |\n")
    lines.append(f"| GST | 18% on brokerage + exchange |\n")
    lines.append(f"| Slippage | 0.05% per side |\n")
    lines.append(f"| **Total round-trip** | **≈{COST_ROUNDTRIP*100:.3f}%** |\n")

    lines += _md_section("Full Period", full, port_full, nifty_full)
    lines += _md_section(f"Period A — before {SPLIT_DATE[:7]} (in-sample proxy)",
                         period_a, port_a, nifty_a)
    lines += _md_section(f"Period B — from {SPLIT_DATE[:7]} onwards (out-of-sample)",
                         period_b, port_b, nifty_b)

    # Honest assessment
    lines.append("\n## Honest Assessment\n\n")
    if port_full:
        beat = port_full["cagr"] > (nifty_full["cagr"] if nifty_full else 0)
        vs   = (f"portfolio CAGR {_p(port_full['cagr'])} vs "
                f"NIFTY 50 {_p(nifty_full['cagr'])}")
        lines.append(f"**Did the strategy beat buy-and-hold?** "
                     f"{'Yes' if beat else 'No'} — {vs} after all costs.\n\n")

        worst_dd = port_full["max_dd"]
        if worst_dd < -0.35:
            dd_take = "psychologically brutal — most investors would have stopped out manually."
        elif worst_dd < -0.20:
            dd_take = "uncomfortable. Expect several months of watching the portfolio bleed."
        else:
            dd_take = "manageable for a disciplined investor who accepts normal market cycles."
        lines.append(f"**Drawdown survivability:** worst portfolio drawdown was "
                     f"{_p(worst_dd)}, which is {dd_take}\n\n")

        if port_b and port_a:
            held_up = port_b["cagr"] > 0
            lines.append(f"**Out-of-sample check:** Period B CAGR is {_p(port_b['cagr'])} — "
                         f"the strategy {'held up' if held_up else 'broke down'} in the recent period.\n\n")

    lines.append("**Caveats to keep in mind:**\n\n")
    lines.append("- Returns are close-to-close; real fills at next-day open will differ slightly.\n")
    lines.append("- Survivorship bias: these 10 stocks are current NIFTY 50 members who survived.\n")
    lines.append("- SMA crossover is a trend-following strategy — it loses in choppy sideways markets.\n")
    lines.append("- A failed backtest is still a successful Phase 2: it cost ₹0 to find out.\n")

    path = RESULTS_DIR / "report.md"
    path.write_text("".join(lines))
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main(symbols=None):
    symbols = symbols or SYMBOLS

    # Load data
    data = {}
    missing = []
    for sym in symbols + ["NIFTY50"]:
        fp = DATA_DIR / f"{sym}.csv"
        if not fp.exists():
            missing.append(sym)
        else:
            data[sym] = pd.read_csv(fp, parse_dates=["date"])

    if missing:
        print(f"Missing data files: {', '.join(missing)}")
        print("Run:  python fetch_data.py")
        return

    nifty_df = data["NIFTY50"]

    trend_note = f" + {TREND_PERIOD}d trend filter" if TREND_PERIOD else ""
    print(f"\nStrategy: SMA {FAST_PERIOD}/{SLOW_PERIOD} crossover{trend_note}")
    print(f"Universe: {', '.join(symbols)}  ({len(symbols)} stocks, equal-weight)")
    print(f"Round-trip cost per trade: ≈{COST_ROUNDTRIP*100:.3f}%  "
          f"(slippage {SLIPPAGE_PER_SIDE*100:.3f}% × 2 + STT + exchange + stamp)\n")
    print("Running backtests...", end=" ", flush=True)

    # Full backtest per stock (returns equity + trades with full date range)
    full_equity  = {}
    full_trades  = {}
    for sym in symbols:
        eq, tr = run_backtest(data[sym])
        full_equity[sym] = eq
        full_trades[sym] = tr

    print("done\n")

    # Compute metrics for each period
    def period_metrics(start=None, end=None):
        return {sym: slice_metrics(full_equity[sym], full_trades[sym], start, end)
                for sym in symbols}

    full_m = period_metrics()
    a_m    = period_metrics(end=SPLIT_DATE)
    b_m    = period_metrics(start=SPLIT_DATE)

    nifty_full = bnh_metrics(nifty_df)
    nifty_a    = bnh_metrics(nifty_df, end=SPLIT_DATE)
    nifty_b    = bnh_metrics(nifty_df, start=SPLIT_DATE)

    # Print
    print_section("Full Period", full_m, portfolio_metrics(full_m), nifty_full)
    print_section(f"Period A — before {SPLIT_DATE[:7]}  (in-sample proxy)",
                  a_m, portfolio_metrics(a_m), nifty_a)
    print_section(f"Period B — from {SPLIT_DATE[:7]}  (out-of-sample)",
                  b_m, portfolio_metrics(b_m), nifty_b)

    # Save
    print()
    path = save_report(symbols, full_m, a_m, b_m, nifty_full, nifty_a, nifty_b)
    print(f"  Report saved → {path}\n")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SMA crossover backtest")
    ap.add_argument("symbols", nargs="*",
                    help="Subset of symbols to test (default: all 10)")
    args = ap.parse_args()
    main(symbols=args.symbols or None)
