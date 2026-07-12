"""cost_gate.py — the intraday / higher-frequency COST GATE (Framework pillar B).

Operationalizes the operator's rule: "B (live intraday) only if it clears the cost
gate." The intraday book was frozen because a real gross edge (+₹11,405) became
−₹21,509 after costs. This module makes that lesson a *reusable, pre-committed
test*: before ANY higher-frequency strategy earns a line of live code, its GROSS
per-trade edge must survive a realistic, deliberately conservative cost model.

The gate answers one question with numbers, not vibes: *after honest costs at the
intended trade frequency and size, is the edge still positive with margin, and is
the net Sharpe still worth running?* Default is FAIL. Parameters are locked in
strategies/SPEC_intraday_cost_gate.md. Pure logic — no I/O, no orders.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# ── Locked gate parameters (SPEC_intraday_cost_gate.md — do not tune to pass) ──
# Per-side cost components as fractions of notional (conservative for NSE intraday).
SLIPPAGE_PER_SIDE = 0.00050    # 5 bps — intraday slippage is worse than delivery
BROKERAGE_PER_SIDE = 0.00030   # ~₹20/lot on intraday turnover, as a fraction proxy
STT_SELL          = 0.00025    # intraday equity STT (sell side)
EXCH_GST          = 0.00010    # exchange txn + GST bundle per side
IMPACT_COEF       = 0.00020    # market-impact add-on scaled by size (see impact())

# Gate thresholds — a strategy must clear ALL.
MIN_NET_EXPECTANCY_R = 0.05    # net expectancy per trade, in R, must exceed this
COST_MARGIN_MULT     = 1.50    # gross edge must exceed cost by at least this factor
MIN_NET_SHARPE       = 0.80    # annualized net Sharpe floor (worth the effort)


@dataclass
class GateInputs:
    gross_ret: pd.Series        # per-trade GROSS return (fraction), one row per trade
    risk: pd.Series             # per-trade risk (R denominator), same index
    trades_per_year: float      # intended frequency (drives Sharpe annualization)
    size_fraction: float = 0.01 # fraction of ADV traded (drives market impact)


def round_trip_cost(size_fraction: float = 0.01) -> float:
    """Total round-trip cost fraction = two sides of fixed costs + size-scaled
    market impact. Deliberately conservative: this is a gate, not a sales pitch."""
    per_side = (SLIPPAGE_PER_SIDE + BROKERAGE_PER_SIDE + EXCH_GST)
    fixed = 2 * per_side + STT_SELL                    # STT sell-side once
    impact = 2 * IMPACT_COEF * (size_fraction / 0.01)  # linear in size vs 1%-ADV base
    return fixed + impact


def evaluate(gi: GateInputs) -> dict:
    """Run the gate. Returns the numbers + PASS/FAIL and the reason per check."""
    g = pd.Series(gi.gross_ret).astype(float).dropna()
    risk = pd.Series(gi.risk).astype(float).reindex(g.index).replace(0.0, np.nan)
    n = len(g)
    cost = round_trip_cost(gi.size_fraction)

    net = g - cost
    R = (net / risk).dropna()
    gross_R = (g / risk).dropna()

    net_exp_R = float(R.mean()) if len(R) else float("nan")
    gross_exp = float(g.mean()) if n else float("nan")
    net_exp = float(net.mean()) if n else float("nan")
    sd = R.std(ddof=1) if len(R) > 1 else np.nan
    net_sharpe = (float(R.mean() / sd) * np.sqrt(gi.trades_per_year)
                  if (sd and sd > 0) else 0.0)

    checks = {
        "net_expectancy_positive": bool(net_exp_R > MIN_NET_EXPECTANCY_R),
        "gross_beats_cost_margin": bool(gross_exp > COST_MARGIN_MULT * cost),
        "net_sharpe_floor": bool(net_sharpe >= MIN_NET_SHARPE),
    }
    passed = all(checks.values())
    return {
        "n_trades": n, "round_trip_cost": round(cost, 5),
        "gross_expectancy": round(gross_exp, 5), "net_expectancy": round(net_exp, 5),
        "net_expectancy_R": round(net_exp_R, 4), "net_sharpe": round(net_sharpe, 3),
        "checks": checks, "passed": passed,
        "verdict": "PASS — eligible to build" if passed else "FAIL — stays frozen",
    }


def format_report(res: dict, name: str = "strategy") -> str:
    c = res["checks"]
    L = [f"# Cost Gate — {name}",
         f"\nTrades: {res['n_trades']}  ·  round-trip cost {res['round_trip_cost']*100:.3f}%",
         f"Gross expectancy {res['gross_expectancy']*100:+.3f}%  →  "
         f"net {res['net_expectancy']*100:+.3f}%  ({res['net_expectancy_R']:+.3f}R)",
         f"Net Sharpe {res['net_sharpe']:.2f}\n",
         f"- [{'PASS' if c['net_expectancy_positive'] else 'FAIL'}] "
         f"net expectancy > {MIN_NET_EXPECTANCY_R}R",
         f"- [{'PASS' if c['gross_beats_cost_margin'] else 'FAIL'}] "
         f"gross edge > {COST_MARGIN_MULT}× round-trip cost",
         f"- [{'PASS' if c['net_sharpe_floor'] else 'FAIL'}] "
         f"net Sharpe ≥ {MIN_NET_SHARPE}",
         f"\n**VERDICT: {res['verdict']}**"]
    return "\n".join(L)
