"""
metrics.py — Reusable research-analytics layer (Research Engine substrate).

Pure, dependency-light functions that turn any strategy's daily **equity curve**
(a pandas Series indexed by date, any starting value) into an institutional
tear sheet: risk/return ratios, benchmark-relative stats, walk-forward stability,
and a Monte Carlo robustness distribution.

Design notes (per the platform's code-quality rules):
  • No I/O, no globals, no strategy knowledge — just numbers in, numbers out, so
    every backtest/paper book can reuse it. Strategy runners import and call it.
  • Everything is computed from the equity curve (and an optional benchmark
    equity curve). Nothing here places an order or fetches data.
  • Honest by construction: ratios return None when the input is too short or
    degenerate (e.g. zero volatility) rather than emitting a misleading number.

Conventions:
  • `periods = 252` trading days/year for annualisation.
  • Risk-free default 6.5% p.a. (matches the options sims' RISK_FREE).
  • "returns" means simple daily returns derived from the equity curve.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252
DEFAULT_RF   = 0.065        # annual risk-free (NSE ~ repo-ish), matches options sims


# ── Building blocks ───────────────────────────────────────────────────────────

def daily_returns(equity: pd.Series) -> pd.Series:
    """Simple daily returns from an equity curve (first day dropped)."""
    return equity.astype(float).pct_change().dropna()


def cagr(equity: pd.Series) -> float | None:
    eq = equity.dropna()
    if len(eq) < 2:
        return None
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years <= 0 or eq.iloc[0] <= 0:
        return None
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1)


def total_return(equity: pd.Series) -> float | None:
    eq = equity.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return None
    return float(eq.iloc[-1] / eq.iloc[0] - 1)


def drawdown_series(equity: pd.Series) -> pd.Series:
    eq = equity.dropna()
    return (eq - eq.cummax()) / eq.cummax()


def max_drawdown(equity: pd.Series) -> float | None:
    dd = drawdown_series(equity)
    return float(dd.min()) if not dd.empty else None


def annual_volatility(returns: pd.Series, periods: int = TRADING_DAYS) -> float | None:
    if len(returns) < 2:
        return None
    return float(returns.std(ddof=1) * np.sqrt(periods))


def sharpe(returns: pd.Series, rf: float = DEFAULT_RF,
           periods: int = TRADING_DAYS) -> float | None:
    """Annualised Sharpe from daily returns (excess over per-day risk-free)."""
    if len(returns) < 2:
        return None
    excess = returns - rf / periods
    sd = excess.std(ddof=1)
    if sd == 0:
        return None
    return float(excess.mean() / sd * np.sqrt(periods))


def sortino(returns: pd.Series, rf: float = DEFAULT_RF,
            periods: int = TRADING_DAYS) -> float | None:
    """Annualised Sortino — downside deviation below the per-day risk-free MAR."""
    if len(returns) < 2:
        return None
    mar = rf / periods
    excess = returns - mar
    downside = excess.clip(upper=0.0)
    dd = np.sqrt((downside ** 2).mean())
    if dd == 0:
        return None
    return float(excess.mean() / dd * np.sqrt(periods))


def calmar(equity: pd.Series) -> float | None:
    """CAGR divided by the magnitude of max drawdown."""
    c, mdd = cagr(equity), max_drawdown(equity)
    if c is None or not mdd:
        return None
    return float(c / abs(mdd))


def recovery_factor(equity: pd.Series) -> float | None:
    tr, mdd = total_return(equity), max_drawdown(equity)
    if tr is None or not mdd:
        return None
    return float(tr / abs(mdd))


def profit_factor(returns: pd.Series) -> float | None:
    """Gross gains / gross losses on daily returns (daily-bar proxy)."""
    gains = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    if losses == 0:
        return None          # no down days in-sample → undefined (not "infinite")
    return float(gains / losses)


def expectancy(returns: pd.Series) -> float | None:
    """Mean return per day (per-bar expectancy)."""
    return float(returns.mean()) if len(returns) else None


def win_rate(returns: pd.Series) -> float | None:
    return float((returns > 0).mean()) if len(returns) else None


def exposure(equity: pd.Series, returns: pd.Series | None = None) -> float | None:
    """Fraction of days the curve actually moved (a proxy for being invested)."""
    r = daily_returns(equity) if returns is None else returns
    return float((r != 0).mean()) if len(r) else None


# ── Benchmark-relative ────────────────────────────────────────────────────────

def _align(returns: pd.Series, bench: pd.Series) -> tuple[pd.Series, pd.Series]:
    df = pd.concat([returns.rename("r"), bench.rename("b")], axis=1).dropna()
    return df["r"], df["b"]


def beta_alpha(returns: pd.Series, bench_returns: pd.Series,
               rf: float = DEFAULT_RF, periods: int = TRADING_DAYS):
    """CAPM beta and annualised alpha vs a benchmark return series."""
    r, b = _align(returns, bench_returns)
    if len(r) < 3:
        return None, None
    var_b = b.var(ddof=1)
    if var_b == 0:
        return None, None
    beta = float(r.cov(b) / var_b)
    mar = rf / periods
    alpha_daily = (r.mean() - mar) - beta * (b.mean() - mar)
    return beta, float(alpha_daily * periods)


def information_ratio(returns: pd.Series, bench_returns: pd.Series,
                      periods: int = TRADING_DAYS) -> float | None:
    """Annualised active-return / tracking-error vs the benchmark."""
    r, b = _align(returns, bench_returns)
    active = r - b
    if len(active) < 2 or active.std(ddof=1) == 0:
        return None
    return float(active.mean() / active.std(ddof=1) * np.sqrt(periods))


# ── Tear sheet ────────────────────────────────────────────────────────────────

def tear_sheet(equity: pd.Series, bench_equity: pd.Series | None = None,
               rf: float = DEFAULT_RF, periods: int = TRADING_DAYS) -> dict:
    """Full institutional metric set for one equity curve (+ optional benchmark)."""
    r = daily_returns(equity)
    m = {
        "start": equity.dropna().index[0].date().isoformat() if len(equity.dropna()) else None,
        "end":   equity.dropna().index[-1].date().isoformat() if len(equity.dropna()) else None,
        "n_days": int(len(r)),
        "cagr": cagr(equity),
        "total_return": total_return(equity),
        "max_drawdown": max_drawdown(equity),
        "annual_vol": annual_volatility(r, periods),
        "sharpe": sharpe(r, rf, periods),
        "sortino": sortino(r, rf, periods),
        "calmar": calmar(equity),
        "recovery_factor": recovery_factor(equity),
        "profit_factor": profit_factor(r),
        "expectancy_daily": expectancy(r),
        "win_rate": win_rate(r),
        "exposure": exposure(equity, r),
        "beta": None, "alpha": None, "information_ratio": None,
    }
    if bench_equity is not None:
        br = daily_returns(bench_equity)
        beta, alpha = beta_alpha(r, br, rf, periods)
        m["beta"], m["alpha"] = beta, alpha
        m["information_ratio"] = information_ratio(r, br, periods)
    return m


# ── Walk-forward stability ────────────────────────────────────────────────────

def walk_forward(equity: pd.Series, n_segments: int = 4,
                 rf: float = DEFAULT_RF, periods: int = TRADING_DAYS) -> list[dict]:
    """Split the curve into N contiguous time segments and report each one's
    CAGR / Sharpe / max-drawdown. For a *parameter-free* rule (like low-vol) this
    is a rolling out-of-sample STABILITY check — it shows whether the edge is
    consistent through time or driven by one lucky window. (It is not a
    parameter re-optimisation walk-forward; there are no parameters to re-fit.)"""
    eq = equity.dropna()
    if len(eq) < n_segments * 2:
        return []
    bounds = np.linspace(0, len(eq), n_segments + 1, dtype=int)
    out = []
    for i in range(n_segments):
        seg = eq.iloc[bounds[i]:bounds[i + 1]]
        if len(seg) < 2:
            continue
        sr = daily_returns(seg)
        out.append({
            "segment": i + 1,
            "start": seg.index[0].date().isoformat(),
            "end": seg.index[-1].date().isoformat(),
            "cagr": cagr(seg),
            "sharpe": sharpe(sr, rf, periods),
            "max_drawdown": max_drawdown(seg),
        })
    return out


# ── Monte Carlo robustness ────────────────────────────────────────────────────

def monte_carlo(returns: pd.Series, n_sims: int = 2000, seed: int = 0,
                periods: int = TRADING_DAYS) -> dict | None:
    """Bootstrap the daily returns (resample with replacement, same horizon) to
    build a distribution of outcomes. Tests how much the realised result depends
    on the specific *sequence* of returns vs the underlying return distribution.
    Reports p5 / p50 / p95 for annualised return (CAGR) and max drawdown."""
    r = returns.dropna().to_numpy()
    n = len(r)
    if n < 20:
        return None
    rng = np.random.default_rng(seed)
    years = n / periods
    cagrs = np.empty(n_sims)
    mdds = np.empty(n_sims)
    for i in range(n_sims):
        sample = rng.choice(r, size=n, replace=True)
        curve = np.cumprod(1.0 + sample)
        cagrs[i] = curve[-1] ** (1 / years) - 1 if curve[-1] > 0 else -1.0
        peak = np.maximum.accumulate(curve)
        mdds[i] = (curve / peak - 1.0).min()

    def pct(a, q):
        return float(np.percentile(a, q))
    return {
        "n_sims": n_sims,
        "cagr_p5": pct(cagrs, 5), "cagr_p50": pct(cagrs, 50), "cagr_p95": pct(cagrs, 95),
        "maxdd_p5": pct(mdds, 5), "maxdd_p50": pct(mdds, 50), "maxdd_p95": pct(mdds, 95),
        "prob_negative_cagr": float((cagrs < 0).mean()),
    }
