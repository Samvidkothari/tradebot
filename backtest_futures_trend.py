"""backtest_futures_trend.py — portfolio backtest of the futures trend sleeve
(Phase 1 prototype). Reads strategies/SPEC_futures_trend.md (pre-registered).

Pipeline (matches the combined-architecture pattern):
  • per market: vol-targeted TS-momentum position (futures_trend.target_position),
    entered next day (shift 1 — no look-ahead);
  • per market: turnover cost on position changes + a per-roll cost;
  • portfolio: equal-risk average across active markets;
  • GOVERNOR (Varma-aligned): scale gross exposure so trailing portfolio vol ≤
    PORT_TARGET_VOL, capped at 1.0 (never lever up — same "never increase
    exposure" discipline as varma_riskstate; NOTE the NIFTY-based varma sizer is
    equity-specific, so a self-contained portfolio vol-target is the correct
    governor for a global futures book);
  • judge after costs vs pre-registered criteria, incl. CORRELATION to the NIFTY
    equity book (the diversification value is the whole point).

DATA SOURCES (honest, per Phase 0):
  --yahoo   : Yahoo pre-stitched continuous futures via futures_data (needs a
              network; run on your Mac). The real prototype.
  --proxy   : (default in a no-network env) treat a few data/*.csv equity series
              as pseudo-'markets' — PLUMBING VALIDATION ONLY, not a futures
              result; it proves the engine runs and the numbers are sane.

READ-ONLY / research. No orders. Writes results/futures_trend_report.md.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import futures_trend as FT
from config import SPLIT_DATE

RESULTS_DIR = Path(__file__).parent / "results"
TRADING_DAYS = 252

# ── Pre-registered portfolio-level parameters (SPEC_futures_trend.md) ──────────
COST_BPS        = 0.0004    # 4 bps per unit turnover (round-trip ~ liquid future)
ROLL_COST_BPS   = 0.0002    # extra bleed charged monthly (calendar roll proxy)
PORT_TARGET_VOL = 0.12      # portfolio annualized vol target (governor)
GOV_VOL_WINDOW  = 60        # trailing window for the governor's vol estimate
MAX_CORR_TO_EQUITY = 0.30   # diversification bar (|corr| to NIFTY daily returns)


# ── Data loaders ──────────────────────────────────────────────────────────────

def _load_yahoo() -> dict:
    import futures_data as FD
    data = FD.load_yahoo_continuous(FD.DEFAULT_GLOBAL_BASKET)
    return {k: v for k, v in data.items() if "close" in v.columns and len(v) > FT.WARMUP}


def _load_proxy() -> dict:
    """Pseudo-'markets' from local equity CSVs — PLUMBING ONLY (not futures)."""
    import data_io
    frames = data_io.symbol_frames(exclude_index=True)
    picks = list(frames)[:8]
    out = {}
    for s in picks:
        df = frames[s].set_index("date")[["close"]].dropna()
        if len(df) > FT.WARMUP:
            out[s] = df
    return out


def _nifty_returns() -> pd.Series:
    import data_io
    nf = data_io.load_nifty()
    s = pd.Series(nf["close"].values, index=pd.to_datetime(nf["date"])).sort_index()
    return s.pct_change()


# ── Backtest ──────────────────────────────────────────────────────────────────

def _market_net_returns(markets: dict) -> pd.DataFrame:
    """Per-market after-cost daily return contribution, aligned on a union index."""
    cols = {}
    for sym, df in markets.items():
        df = df.sort_index()
        close = df["close"].astype(float)
        pos = FT.target_position(df).shift(1)               # enter next day
        ret = close.pct_change()
        gross = pos * ret
        turnover = pos.diff().abs().fillna(0.0)
        cost = turnover * COST_BPS
        # monthly roll cost proxy: charge on the first session of each month
        month = close.index.to_series().dt.to_period("M")
        roll_day = month.ne(month.shift(1)).values
        cost = cost + pd.Series(np.where(roll_day, ROLL_COST_BPS, 0.0), index=close.index)
        cols[sym] = (gross - cost)
    return pd.DataFrame(cols).sort_index()


def _governor(port_ret: pd.Series) -> pd.Series:
    """Scale gross so trailing vol ≤ PORT_TARGET_VOL, capped at 1.0 (never lever
    up). Applied with a one-day lag (uses only past vol)."""
    rv = port_ret.rolling(GOV_VOL_WINDOW).std(ddof=1) * np.sqrt(TRADING_DAYS)
    scale = (PORT_TARGET_VOL / rv.replace(0.0, np.nan)).clip(upper=1.0)
    return scale.shift(1).fillna(1.0).clip(upper=1.0)


def run(markets: dict):
    net = _market_net_returns(markets)
    if net.empty:
        return None
    # equal-risk average across the markets active each day
    port_raw = net.mean(axis=1, skipna=True).fillna(0.0)
    gov = _governor(port_raw)
    port = port_raw * gov
    equity = (1 + port).cumprod()
    return {"equity": equity, "port_ret": port, "gov": gov, "n_markets": net.shape[1]}


# ── Metrics ───────────────────────────────────────────────────────────────────

def _metrics(equity: pd.Series, ret: pd.Series, start=None):
    eq, r = equity, ret
    if start:
        eq = eq[eq.index >= pd.Timestamp(start)]
        r = r[r.index >= pd.Timestamp(start)]
    if len(eq) < 3:
        return None
    eq = eq / eq.iloc[0]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    dd = (eq / eq.cummax() - 1).min()
    sd = r.std(ddof=1)
    return {"cagr": float(eq.iloc[-1] ** (1 / yrs) - 1) if yrs > 0 else 0.0,
            "sharpe": float(r.mean() / sd * np.sqrt(TRADING_DAYS)) if sd else 0.0,
            "max_dd": float(dd), "years": round(yrs, 1)}


def _corr_to_equity(port_ret: pd.Series) -> float:
    nifty = _nifty_returns().reindex(port_ret.index)
    j = pd.concat([port_ret, nifty], axis=1).dropna()
    if len(j) < 30:
        return float("nan")
    return float(j.iloc[:, 0].corr(j.iloc[:, 1]))


def _p(x):
    return "n/a" if x is None else f"{x*100:+.1f}%"


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    mode = "yahoo" if "--yahoo" in sys.argv else "proxy"
    markets = _load_yahoo() if mode == "yahoo" else _load_proxy()
    proxy = (mode == "proxy")

    if not markets:
        print("  No usable market data (network? run --yahoo on a networked machine).")
        return "NO-DATA"

    res = run(markets)
    full = _metrics(res["equity"], res["port_ret"])
    oos = _metrics(res["equity"], res["port_ret"], start=SPLIT_DATE)
    corr = _corr_to_equity(res["port_ret"])
    W = 92

    checks = [
        ("OOS return positive after costs", bool(oos and oos["cagr"] > 0)),
        ("OOS Sharpe >= 0.5", bool(oos and oos["sharpe"] >= 0.5)),
        (f"|corr| to equity <= {MAX_CORR_TO_EQUITY} (diversifier)",
         bool(not np.isnan(corr) and abs(corr) <= MAX_CORR_TO_EQUITY)),
    ]
    verdict = "PASS" if all(ok for _, ok in checks) else "FAIL"
    if proxy:
        verdict = "PLUMBING-OK (proxy data — NOT a futures verdict)"

    print(f"\n{'='*W}\n  FUTURES TREND SLEEVE — Phase 1 ({'PROXY plumbing' if proxy else 'Yahoo continuous'})\n{'='*W}")
    if proxy:
        print("  ⚠ PROXY MODE: local equity CSVs as pseudo-markets — validates the")
        print("    engine only. Run `python backtest_futures_trend.py --yahoo` on a")
        print("    networked machine for the real global-futures prototype.")
    print(f"  Markets: {res['n_markets']}   ·   governor mean scale {res['gov'].mean():.2f}   ·   OOS split {SPLIT_DATE}")
    print(f"  Full : CAGR {_p(full and full['cagr'])}  Sharpe {full['sharpe'] if full else 0:.2f}  MaxDD {_p(full and full['max_dd'])}")
    print(f"  OOS  : CAGR {_p(oos and oos['cagr'])}  Sharpe {oos['sharpe'] if oos else 0:.2f}  MaxDD {_p(oos and oos['max_dd'])}")
    print(f"  Corr to NIFTY equity: {corr:.2f}")
    print(f"  {'-'*W}")
    for name, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  VERDICT: {verdict}\n{'='*W}\n")

    md = [
        "# Futures Trend Sleeve — Phase 1 Backtest\n\n",
        f"Generated: {date.today()}  ·  mode: **{'PROXY (plumbing only)' if proxy else 'Yahoo continuous'}**  \n",
        "Spec: `strategies/SPEC_futures_trend.md` (pre-registered).  \n",
        "Per-market vol-targeted TS-momentum → equal-risk portfolio → portfolio "
        "vol-target governor (≤1.0) → after roll+turnover costs.\n\n",
        ("> ⚠ **Proxy run:** local equity series stand in for futures markets. This "
         "validates the engine and cost/vol plumbing ONLY — it is **not** a futures "
         "result. Run `--yahoo` on a networked machine for the real prototype.\n\n"
         if proxy else
         "> Data: Yahoo pre-stitched continuous futures (prototype quality; a true "
         "back-adjusted sleeve needs individual contracts — see `results/futures_phase0.md`).\n\n"),
        f"## Verdict: **{verdict}**\n\n",
        "| Window | CAGR | Sharpe | Max DD |\n|---|---|---|---|\n",
        f"| Full | {_p(full and full['cagr'])} | {full['sharpe'] if full else 0:.2f} | {_p(full and full['max_dd'])} |\n",
        f"| Out-of-sample (≥{SPLIT_DATE[:7]}) | {_p(oos and oos['cagr'])} | {oos['sharpe'] if oos else 0:.2f} | {_p(oos and oos['max_dd'])} |\n",
        f"\n**Correlation to NIFTY equity book:** {corr:.2f} "
        f"(diversification bar |corr| ≤ {MAX_CORR_TO_EQUITY}).\n\n",
        "### Pre-committed criteria\n\n",
    ]
    for name, ok in checks:
        md.append(f"- {'✅' if ok else '❌'} {name}\n")
    md.append("\nParameters are locked in the SPEC; not tuned to this result. "
              "Default verdict is reject; human-only promotion.\n")
    (RESULTS_DIR / "futures_trend_report.md").write_text("".join(md))
    print(f"  Report saved → results/futures_trend_report.md\n")
    return verdict


if __name__ == "__main__":
    main()
