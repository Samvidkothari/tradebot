"""
portfolio_analyzer.py — Portfolio / correlation analytics (Research Engine).

Reads the live low-vol paper book (portfolio.db, read-only), marks it to the
latest prices, and decomposes its RISK and DIVERSIFICATION from the return panel:

  • Correlation        — pairwise correlation matrix + average pairwise correlation
                         (the headline diversification number).
  • Concentration      — Herfindahl index, effective number of holdings, top weight.
  • Risk decomposition — each holding's % contribution to portfolio variance
                         (reveals that equal *weight* != equal *risk*).
  • Diversification    — portfolio vol vs weighted-average constituent vol.
  • Allocation compare — equal-weight (actual) vs inverse-volatility, side by side,
                         so you can see what a risk-based weighting would change.
  • Sector exposure    — via a best-effort static NIFTY sector map (clearly caveated).

The maths lives in pure, testable functions (weights + covariance in, numbers out).
RESEARCH ONLY: read-only, places no orders, and does NOT alter the pre-registered
equal-weight low-vol book — the alternative weightings are shown for insight, not
applied.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import data_io

BASE        = Path(__file__).parent
RESULTS_DIR = BASE / "results"
TRADING_DAYS = 252

# Best-effort STATIC sector map for the NIFTY universe. Public knowledge, changes
# rarely; treat as approximate (membership/sector can drift). Unmapped -> "Other".
SECTOR_MAP = {
    "HDFCBANK": "Financials", "ICICIBANK": "Financials", "KOTAKBANK": "Financials",
    "AXISBANK": "Financials", "SBIN": "Financials", "INDUSINDBK": "Financials",
    "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials", "SHRIRAMFIN": "Financials",
    "HDFCLIFE": "Financials", "SBILIFE": "Financials", "JIOFIN": "Financials",
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT", "TECHM": "IT", "LTIM": "IT",
    "RELIANCE": "Energy", "ONGC": "Energy",
    "NTPC": "Utilities", "POWERGRID": "Utilities",
    "COALINDIA": "Metals & Mining", "TATASTEEL": "Metals & Mining",
    "JSWSTEEL": "Metals & Mining", "HINDALCO": "Metals & Mining",
    "MARUTI": "Auto", "M&M": "Auto", "TATAMOTORS": "Auto", "BAJAJ-AUTO": "Auto",
    "EICHERMOT": "Auto", "HEROMOTOCO": "Auto",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "TATACONSUM": "FMCG",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "APOLLOHOSP": "Healthcare",
    "ULTRACEMCO": "Materials", "GRASIM": "Materials", "ASIANPAINT": "Materials",
    "LT": "Infra", "ADANIENT": "Infra", "ADANIPORTS": "Infra",
    "BHARTIARTL": "Telecom", "TITAN": "Consumer", "TRENT": "Consumer", "BEL": "Defense",
}


# ── Pure portfolio maths ──────────────────────────────────────────────────────

def annualized_cov(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.cov() * TRADING_DAYS


def portfolio_vol(weights: np.ndarray, cov: np.ndarray) -> float:
    return float(np.sqrt(weights @ cov @ weights))


def risk_contributions(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Each asset's fraction of total portfolio variance (sums to 1.0)."""
    var = weights @ cov @ weights
    if var <= 0:
        return np.zeros_like(weights)
    return (weights * (cov @ weights)) / var


def hhi(weights: np.ndarray) -> float:
    return float(np.sum(weights ** 2))


def effective_n(weights: np.ndarray) -> float:
    h = hhi(weights)
    return float(1.0 / h) if h > 0 else 0.0


def avg_pairwise_corr(corr: pd.DataFrame) -> float:
    a = corr.to_numpy()
    iu = np.triu_indices_from(a, k=1)
    return float(np.mean(a[iu])) if len(iu[0]) else float("nan")


def diversification_ratio(weights: np.ndarray, vols: np.ndarray, cov: np.ndarray) -> float:
    """Weighted-avg constituent vol / portfolio vol. >1 = diversification benefit."""
    pv = portfolio_vol(weights, cov)
    return float((weights @ vols) / pv) if pv > 0 else float("nan")


def inverse_vol_weights(vols: np.ndarray) -> np.ndarray:
    inv = 1.0 / vols
    return inv / inv.sum()


# ── Data ──────────────────────────────────────────────────────────────────────

def load_closes(symbols):
    return data_io.close_panel(symbols=symbols)


def load_holdings():
    db = BASE / "portfolio.db"
    if not db.exists():
        return []
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT symbol, qty, avg_price FROM positions ORDER BY symbol")]
    finally:
        c.close()


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze():
    holdings = load_holdings()
    if not holdings:
        return {"error": "No holdings — run paper_trader.py first."}

    syms = [h["symbol"] for h in holdings]
    closes = load_closes(syms)
    syms = [s for s in syms if s in closes.columns]      # keep only price-available
    closes = closes[syms]
    last = closes.iloc[-1]

    # Actual (equal-weight low-vol book) market-value weights.
    mv = np.array([h["qty"] * float(last[h["symbol"]]) for h in holdings if h["symbol"] in syms])
    w = mv / mv.sum()

    rets = closes.pct_change().dropna()
    corr = rets.corr()
    cov = annualized_cov(rets).to_numpy()
    vols = np.sqrt(np.diag(cov))

    rc = risk_contributions(w, cov)
    inv_w = inverse_vol_weights(vols)

    # Sector exposure (best-effort).
    sectors = {}
    for s, wi in zip(syms, w):
        sectors[SECTOR_MAP.get(s, "Other")] = sectors.get(SECTOR_MAP.get(s, "Other"), 0.0) + float(wi)

    def alloc_summary(weights):
        return {"portfolio_vol": portfolio_vol(weights, cov),
                "effective_n": effective_n(weights),
                "max_weight": float(weights.max()),
                "diversification_ratio": diversification_ratio(weights, vols, cov)}

    holdings_tbl = sorted(
        [{"symbol": s, "weight": float(wi), "ann_vol": float(v),
          "risk_contribution": float(r)}
         for s, wi, v, r in zip(syms, w, vols, rc)],
        key=lambda d: d["risk_contribution"], reverse=True)

    return {
        "generated": date.today().isoformat(),
        "as_of": closes.index[-1].date().isoformat(),
        "n_holdings": len(syms),
        "avg_pairwise_corr": avg_pairwise_corr(corr),
        "concentration": {"hhi": hhi(w), "effective_n": effective_n(w),
                          "max_weight": float(w.max()),
                          "top5_weight": float(np.sort(w)[::-1][:5].sum())},
        "actual_equal_weight": alloc_summary(w),
        "inverse_vol": alloc_summary(inv_w),
        "holdings": holdings_tbl,
        "sectors": dict(sorted(sectors.items(), key=lambda kv: kv[1], reverse=True)),
        "corr_labels": syms,
        "corr_matrix": [[round(float(corr.iloc[i, j]), 2) for j in range(len(syms))]
                        for i in range(len(syms))],
    }


def main():
    RESULTS_DIR.mkdir(exist_ok=True)
    a = analyze()
    if "error" in a:
        print(a["error"]); return

    print(f"\n{'='*68}\n  PORTFOLIO ANALYSIS — low-vol book as of {a['as_of']}  (research only)\n{'='*68}")
    print(f"  Holdings: {a['n_holdings']}   avg pairwise corr: {a['avg_pairwise_corr']:.2f}")
    c = a["concentration"]
    print(f"  Concentration: effective N {c['effective_n']:.1f} of {a['n_holdings']}   "
          f"max weight {c['max_weight']*100:.1f}%   top-5 {c['top5_weight']*100:.0f}%")
    print(f"\n  Allocation         PortVol   EffN   MaxWt   DivRatio")
    for label, key in [("Equal-weight (actual)", "actual_equal_weight"),
                       ("Inverse-volatility", "inverse_vol")]:
        s = a[key]
        print(f"  {label:<20}{s['portfolio_vol']*100:>7.1f}%{s['effective_n']:>7.1f}"
              f"{s['max_weight']*100:>7.1f}%{s['diversification_ratio']:>10.2f}")
    print(f"\n  Top risk contributors (equal weight):")
    for h in a["holdings"][:5]:
        print(f"    {h['symbol']:<12} weight {h['weight']*100:4.1f}%  "
              f"vol {h['ann_vol']*100:4.1f}%  risk {h['risk_contribution']*100:4.1f}%")
    print(f"\n  Sector exposure: " +
          ", ".join(f"{k} {v*100:.0f}%" for k, v in list(a["sectors"].items())[:6]))
    print(f"  (sector map is best-effort/static)\n{'='*68}\n")

    (RESULTS_DIR / "portfolio.json").write_text(json.dumps(a, indent=2))
    print(f"  Saved → results/portfolio.json\n")


if __name__ == "__main__":
    main()
