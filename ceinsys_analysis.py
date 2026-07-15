"""ceinsys_analysis.py — honest, after-cost study of a CEINSYS swing thesis.

The user's objective: "at least +20% from CEINSYS over ~5 months." No strategy can
*guarantee* that. This module does the honest thing instead — it measures, on
CEINSYS's own history, how realistic that target is and what a disciplined,
rule-based swing would have done, net of real costs. READ-ONLY / research: reads
data/CEINSYS.csv, writes results/ceinsys_report.md, places NO orders.

Three parts:
  A. Price-action swing backtest on CEINSYS — reuses the PRE-REGISTERED signal
     logic in priceaction.py (no rule reimplemented), after config.COST_ROUNDTRIP.
     One small-cap yields few trades, so this is descriptive, not a pass/fail.
  B. 5-month target study — the core answer. From every historical day, look
     forward HORIZON trading days and ask: how often did CEINSYS deliver +20%?
     Two lenses: point-to-point return, and "did it TOUCH +20% intramonth" (MFE),
     which is what a target-exit actually captures. Also the downside: how often
     it was down 20%+. Repeated conditioned on a simple 200-DMA trend filter to
     show how entry timing shifts the odds.
  C. Live plan — latest price, trend state, an ATR-based entry/stop/target and
     position size for a given capital at a fixed risk budget, plus the plain
     arithmetic of the +20% goal. A TARGET, explicitly not a guarantee.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config
import priceaction as PA
from trailing_exit import atr as _atr

DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"
SYMBOL = "CEINSYS"

COST = config.COST_ROUNDTRIP
SPLIT = pd.Timestamp(config.SPLIT_DATE)

# ── Objective knobs (config, not tuned to results) ───────────────────────────
HORIZON_DAYS = 105        # ~5 months of ~21 trading days/month
TARGET_RET = 0.20         # the +20% objective
RISK_PER_TRADE = 0.01     # 1% of capital risked to the initial stop
ATR_STOP_MULT = 2.0       # initial stop = entry - ATR_STOP_MULT * ATR(14)
TREND_MA = 200            # trend filter: close vs 200-day MA


# ── data ──────────────────────────────────────────────────────────────────────

def load() -> pd.DataFrame:
    fp = DATA_DIR / f"{SYMBOL}.csv"
    if not fp.exists():
        raise FileNotFoundError(
            f"data/{SYMBOL}.csv not found — run:  python fetch_ceinsys.py")
    df = (pd.read_csv(fp, parse_dates=["date"])
          .sort_values("date").set_index("date"))
    return df


# ── A. price-action backtest on CEINSYS ─────────────────────────────────────

def price_action_backtest(df: pd.DataFrame) -> dict:
    trades = PA.generate_trades(df)
    rows = []
    for t in trades:
        net = t["gross_ret"] - COST
        r = net / t["risk"] if t["risk"] > 0 else 0.0
        rows.append({"exit_date": pd.Timestamp(t["exit_date"]), "side": t["side"],
                     "reason": t["reason"], "net_ret": net, "R": r})
    td = pd.DataFrame(rows)
    if td.empty:
        return {"n": 0, "trades": td}
    R = td["R"]
    wins, losses = R[R > 0], R[R <= 0]
    eq = (1 + RISK_PER_TRADE * R).cumprod()
    maxdd = float((eq / eq.cummax() - 1).min())
    return {
        "n": int(len(td)), "trades": td,
        "expectancy_R": float(R.mean()),
        "win_rate": float((R > 0).mean()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() else float("inf"),
        "compounded": float(eq.iloc[-1] - 1),
        "max_dd": maxdd,
        "oos_n": int((td["exit_date"] >= SPLIT).sum()),
        "oos_expectancy_R": float(R[td["exit_date"] >= SPLIT].mean())
        if (td["exit_date"] >= SPLIT).any() else float("nan"),
    }


# ── B. 5-month target study ─────────────────────────────────────────────────

def horizon_study(df: pd.DataFrame, mask: pd.Series | None = None) -> dict:
    """Forward-looking DESCRIPTIVE stats over HORIZON_DAYS. For each start day with
    a full forward window, compute the point-to-point return, the max favourable
    excursion (highest high / start close - 1) and max adverse excursion."""
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    n = len(close)
    p2p, mfe, mae = [], [], []
    idx = range(n - HORIZON_DAYS)
    keep = mask.to_numpy() if mask is not None else np.ones(n, bool)
    for i in idx:
        if not keep[i]:
            continue
        c0 = close[i]
        window_hi = high[i + 1:i + 1 + HORIZON_DAYS]
        window_lo = low[i + 1:i + 1 + HORIZON_DAYS]
        cend = close[i + HORIZON_DAYS]
        p2p.append(cend / c0 - 1)
        mfe.append(window_hi.max() / c0 - 1)
        mae.append(window_lo.min() / c0 - 1)
    if not p2p:
        return {"n": 0}
    p2p, mfe, mae = np.array(p2p), np.array(mfe), np.array(mae)
    return {
        "n": int(len(p2p)),
        "p_touch_target": float((mfe >= TARGET_RET).mean()),   # touched +20% intra-window
        "p_close_target": float((p2p >= TARGET_RET).mean()),   # ended +20% or better
        "p_down_20": float((mae <= -TARGET_RET).mean()),       # dropped 20%+ at some point
        "median_p2p": float(np.median(p2p)),
        "mean_p2p": float(p2p.mean()),
        "worst_p2p": float(p2p.min()),
        "best_p2p": float(p2p.max()),
        "median_mfe": float(np.median(mfe)),
        "median_mae": float(np.median(mae)),
    }


# ── C. live plan ─────────────────────────────────────────────────────────────

def live_plan(df: pd.DataFrame, capital: float = config.PAPER_CAPITAL) -> dict:
    c = df["close"]
    last = float(c.iloc[-1])
    ma = float(c.rolling(TREND_MA).mean().iloc[-1]) if len(c) >= TREND_MA else float("nan")
    a = float(_atr(df["high"], df["low"], df["close"], 14)[-1])
    entry = last
    stop = entry - ATR_STOP_MULT * a
    risk_per_share = entry - stop
    qty = int((capital * RISK_PER_TRADE) // risk_per_share) if risk_per_share > 0 else 0
    pos_value = qty * entry
    target_price = entry * (1 + TARGET_RET)
    return {
        "last": last, "ma200": ma,
        "trend": "UP (above 200DMA)" if (not np.isnan(ma) and last > ma)
        else "DOWN/weak (below 200DMA)" if not np.isnan(ma) else "n/a (short history)",
        "atr14": a, "entry": entry, "stop": stop, "risk_per_share": risk_per_share,
        "target_price": target_price, "qty": qty, "pos_value": pos_value,
        "capital_at_risk": qty * risk_per_share,
        "pct_of_capital": (pos_value / capital) if capital else 0.0,
    }


# ── report ───────────────────────────────────────────────────────────────────

def _pct(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:+.1f}%"


def build_report(df: pd.DataFrame) -> str:
    pa = price_action_backtest(df)
    all_h = horizon_study(df)
    up_mask = df["close"] > df["close"].rolling(TREND_MA).mean()
    up_h = horizon_study(df, up_mask)
    plan = live_plan(df)
    span = f"{df.index.min().date()} → {df.index.max().date()}  ({len(df)} bars)"

    L = []
    L.append(f"# CEINSYS — Swing Thesis Study\n")
    L.append(f"Generated {datetime.now():%Y-%m-%d %H:%M}  ·  Data span: {span}  ")
    L.append(f"Costs: {COST*100:.3f}% per round trip (config.COST_ROUNDTRIP).  ")
    L.append("**Paper/research only — this file and this repo place no orders.**\n")

    L.append("## The objective, stated honestly\n")
    L.append(f"Goal: **+{TARGET_RET*100:.0f}% over ~5 months** ({HORIZON_DAYS} trading days). "
             "No strategy can guarantee this. Below is what CEINSYS's own history says "
             "about how often that target was reachable, and a disciplined rule set that "
             "*targets* it with a defined stop.\n")

    L.append(f"## A. Price-action swing backtest (pre-registered rules) on {SYMBOL}\n")
    if pa["n"] == 0:
        L.append("_No mechanical price-action trades triggered on CEINSYS over this "
                 "span._ A single small-cap rarely produces enough setups to validate a "
                 "rule — this is descriptive, not a verdict.\n")
    else:
        L.append(f"- Trades: **{pa['n']}** (out-of-sample ≥{config.SPLIT_DATE}: {pa['oos_n']})")
        L.append(f"- Expectancy: **{pa['expectancy_R']:+.3f}R** per trade "
                 f"(OOS {pa['oos_expectancy_R']:+.3f}R)")
        L.append(f"- Win rate: {pa['win_rate']*100:.0f}%  ·  Profit factor: {pa['profit_factor']:.2f}")
        L.append(f"- Compounded at {RISK_PER_TRADE*100:.0f}% risk/trade: **{_pct(pa['compounded'])}** "
                 f"·  Max drawdown: {_pct(pa['max_dd'])}")
        L.append(f"\n_Only {pa['n']} trades — treat as a sanity check, not proof of edge._\n")

    L.append("## B. Will CEINSYS give +20% in 5 months? (its own history)\n")
    L.append("| Window from every day | All days | Only when above 200-DMA |")
    L.append("|---|---|---|")

    def row(label, key, fmt=_pct):
        a = all_h.get(key); u = up_h.get(key)
        av = f"{a*100:.0f}%" if ("p_" in key) and a is not None else (fmt(a) if a is not None else "n/a")
        uv = f"{u*100:.0f}%" if ("p_" in key) and u is not None else (fmt(u) if u is not None else "n/a")
        return f"| {label} | {av} | {uv} |"

    if all_h.get("n"):
        L.append(row(f"P(touches +{TARGET_RET*100:.0f}% intra-window)", "p_touch_target"))
        L.append(row(f"P(ends ≥ +{TARGET_RET*100:.0f}%)", "p_close_target"))
        L.append(row(f"P(drops −{TARGET_RET*100:.0f}%+ at some point)", "p_down_20"))
        L.append(row("Median 5-month return", "median_p2p"))
        L.append(row("Best / typical upside (median MFE)", "median_mfe"))
        L.append(row("Typical drawdown inside window (median MAE)", "median_mae"))
        L.append(row("Worst 5-month return", "worst_p2p"))
        L.append(f"\nSample: {all_h['n']} overlapping 5-month windows "
                 f"({up_h.get('n',0)} of them started above the 200-DMA).\n")
        L.append("**Read this carefully:** 'touches +20%' counts windows where the price "
                 "hit +20% at any point (a target-exit could capture it); 'ends ≥+20%' is a "
                 "pure buy-and-hold. The gap between them, and the P(−20% drawdown) row, is "
                 "the risk you are taking to chase the reward.\n")
    else:
        L.append("| _insufficient history_ | | |\n")

    L.append("## C. If you act on it — a disciplined plan (targets, not promises)\n")
    L.append(f"- Latest close: **₹{plan['last']:.1f}**  ·  200-DMA: "
             f"{'₹%.1f' % plan['ma200'] if not np.isnan(plan['ma200']) else 'n/a'}  "
             f"·  Trend: **{plan['trend']}**")
    L.append(f"- ATR(14): ₹{plan['atr14']:.1f}  →  initial stop = entry − "
             f"{ATR_STOP_MULT}×ATR = **₹{plan['stop']:.1f}** (risk ₹{plan['risk_per_share']:.1f}/share)")
    L.append(f"- +20% target price: **₹{plan['target_price']:.1f}**")
    L.append(f"- Sizing on ₹{config.PAPER_CAPITAL:,.0f} paper capital at "
             f"{RISK_PER_TRADE*100:.0f}% risk: **{plan['qty']} shares** "
             f"(≈₹{plan['pos_value']:,.0f}, {plan['pct_of_capital']*100:.0f}% of capital, "
             f"₹{plan['capital_at_risk']:,.0f} at risk to the stop)")
    L.append("")
    if not np.isnan(plan["ma200"]) and plan["last"] < plan["ma200"]:
        L.append("> ⚠️ Price is **below** its 200-DMA. Per column B, the +20% odds are "
                 "materially worse when entering below trend. Discipline says wait for a "
                 "reclaim of the 200-DMA rather than catching a falling knife.\n")
    L.append("### Rules of engagement\n")
    L.append(f"1. **Only enter with trend** — close above the {TREND_MA}-DMA.")
    L.append(f"2. **Hard stop** at ₹{plan['stop']:.1f} (−{ATR_STOP_MULT}×ATR). If hit, you "
             f"lose ~{RISK_PER_TRADE*100:.0f}% of capital — no averaging down.")
    L.append(f"3. **Target** +{TARGET_RET*100:.0f}% (₹{plan['target_price']:.1f}); consider "
             "trailing the rest with trailing_exit.py to ride a bigger move.")
    L.append(f"4. **Time stop** — if neither stop nor target in {HORIZON_DAYS} days, exit and "
             "redeploy; the thesis was that it moves within ~5 months.")
    L.append("5. **Position cap** — one small-cap should be a small slice of the book, not "
             "the book. Size for the stop, not for the dream.\n")

    L.append("---\n")
    L.append("_Not investment advice. A ₹1,900-cr small-cap can move >9% in a week; a −20% "
             "outcome is as real as the +20% one. Targets describe intent, never certainty._")
    return "\n".join(L)


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    df = load()
    report = build_report(df)
    out = RESULTS_DIR / "ceinsys_report.md"
    out.write_text(report)
    # console summary
    print("\n" + "=" * 78)
    print(f"  CEINSYS swing study — {df.index.min().date()} → {df.index.max().date()} "
          f"({len(df)} bars)")
    print("=" * 78)
    h = horizon_study(df)
    if h.get("n"):
        print(f"  P(+{int(TARGET_RET*100)}% touched within 5 months): "
              f"{h['p_touch_target']*100:.0f}%   "
              f"P(down 20%+ at some point): {h['p_down_20']*100:.0f}%")
        print(f"  Median 5-month return: {h['median_p2p']*100:+.1f}%   "
              f"Worst: {h['worst_p2p']*100:+.1f}%")
    print(f"  Full report → results/ceinsys_report.md")
    print("=" * 78 + "\n")


if __name__ == "__main__":
    main()
