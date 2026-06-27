"""
risk_analytics.py — Risk-analytics module (Research Engine).

Quantifies the risk of a strategy's return/equity series: Value-at-Risk and
Expected Shortfall, drawdown analytics (depth, duration, Ulcer index, time
underwater), tail statistics, a volatility-targeting scale, and ATR-based
position sizing. Pure, testable functions — series in, numbers out.

SCOPE / BOUNDARY:
This is RESEARCH measurement only. The prompt's "Emergency Kill Switch" and
"Trading Halt Conditions" are LIVE-Trading-Engine controls — they belong to the
gated execution layer, not here, and nothing in this module is wired to place,
modify, or stop an order (there is no live trading). Volatility targeting and
ATR sizing are shown as analysis; they are NOT applied to the pre-registered
equal-weight book.

Conventions: VaR/CVaR are reported as POSITIVE daily loss fractions (0.018 = a
1.8% loss). 252 trading days/year for annualisation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import metrics as M

TRADING_DAYS = 252
_Z = {0.95: 1.6448536269514722, 0.99: 2.3263478740408408}   # one-sided normal


# ── Value-at-Risk / Expected Shortfall ────────────────────────────────────────

def historical_var(returns: pd.Series, conf: float = 0.95) -> float | None:
    """Historical VaR: the loss not exceeded with `conf` probability (positive)."""
    r = returns.dropna()
    if len(r) < 20:
        return None
    return float(-np.quantile(r, 1.0 - conf))


def cvar(returns: pd.Series, conf: float = 0.95) -> float | None:
    """Expected shortfall: mean loss in the worst (1-conf) tail (positive)."""
    r = returns.dropna()
    if len(r) < 20:
        return None
    cutoff = np.quantile(r, 1.0 - conf)
    tail = r[r <= cutoff]
    return float(-tail.mean()) if len(tail) else None


def parametric_var(returns: pd.Series, conf: float = 0.95) -> float | None:
    """Gaussian VaR = z*sigma - mu (positive loss)."""
    r = returns.dropna()
    if len(r) < 20:
        return None
    return float(_Z.get(conf, 1.645) * r.std(ddof=1) - r.mean())


# ── Drawdown analytics ────────────────────────────────────────────────────────

def ulcer_index(equity: pd.Series) -> float | None:
    """RMS of the percentage drawdown path — penalises deep, long drawdowns."""
    dd = M.drawdown_series(equity)
    if dd.empty:
        return None
    return float(np.sqrt((dd ** 2).mean()))


def drawdown_stats(equity: pd.Series) -> dict:
    eq = equity.dropna()
    dd = M.drawdown_series(eq)
    underwater = dd < -1e-12
    # Longest / average underwater run length (in observations).
    runs, cur = [], 0
    for u in underwater:
        if u:
            cur += 1
        elif cur:
            runs.append(cur); cur = 0
    if cur:
        runs.append(cur)
    return {
        "max_drawdown": M.max_drawdown(eq),
        "current_drawdown": float(dd.iloc[-1]) if len(dd) else None,
        "ulcer_index": ulcer_index(eq),
        "time_in_drawdown": float(underwater.mean()) if len(underwater) else None,
        "longest_dd_days": int(max(runs)) if runs else 0,
        "avg_dd_days": float(np.mean(runs)) if runs else 0.0,
    }


# ── Tail statistics ───────────────────────────────────────────────────────────

def tail_stats(returns: pd.Series) -> dict:
    r = returns.dropna()
    if len(r) < 3:
        return {}
    downside = r[r < 0]
    return {
        "daily_vol": float(r.std(ddof=1)),
        "ann_vol": float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)),
        "skew": float(r.skew()),
        "kurtosis": float(r.kurtosis()),               # excess kurtosis
        "downside_dev": float(np.sqrt((downside ** 2).mean())) if len(downside) else 0.0,
        "worst_day": float(r.min()),
        "best_day": float(r.max()),
    }


# ── Volatility targeting (research illustration) ──────────────────────────────

def vol_target_scale(returns: pd.Series, target_ann_vol: float = 0.10) -> dict:
    """Scale factor that would map realised annualised vol to `target_ann_vol`.
    >1 means lever up, <1 means de-risk. Illustration only — not applied."""
    r = returns.dropna()
    realised = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(r) > 1 else None
    if not realised:
        return {"realised_ann_vol": None, "target_ann_vol": target_ann_vol, "scale": None}
    return {"realised_ann_vol": realised, "target_ann_vol": target_ann_vol,
            "scale": float(target_ann_vol / realised)}


# ── ATR & position sizing (research utility) ──────────────────────────────────

def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> float | None:
    """Average True Range over `window` (simple mean of true range)."""
    h, l, c = high.dropna(), low.dropna(), close.dropna()
    if len(c) < window + 1:
        return None
    prev_close = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_close).abs(), (l - prev_close).abs()],
                   axis=1).max(axis=1)
    return float(tr.dropna().iloc[-window:].mean())


def position_size_atr(capital: float, risk_pct: float, atr_value: float,
                      multiplier: float = 2.0) -> dict:
    """Units to buy so that an `multiplier`*ATR adverse move loses `risk_pct` of
    capital — the classic volatility-based sizing. Illustration only."""
    if atr_value is None or atr_value <= 0:
        return {"units": 0, "risk_budget": 0.0}
    risk_budget = capital * risk_pct
    stop_distance = multiplier * atr_value
    return {"units": int(risk_budget / stop_distance),
            "risk_budget": float(risk_budget), "stop_distance": float(stop_distance)}


# ── Bundled report for one equity curve ───────────────────────────────────────

def risk_report(equity: pd.Series) -> dict:
    r = M.daily_returns(equity)
    return {
        "var": {"hist_95": historical_var(r, 0.95), "hist_99": historical_var(r, 0.99),
                "param_95": parametric_var(r, 0.95), "cvar_95": cvar(r, 0.95)},
        "drawdown": drawdown_stats(equity),
        "tail": tail_stats(r),
        "vol_target_10pct": vol_target_scale(r, 0.10),
    }
