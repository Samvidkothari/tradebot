"""
attribution.py — Performance attribution (Research Engine).

Decomposes WHERE a monthly-rebalanced strategy's return comes from:

  • Holding contribution  — each stock's summed contribution (weight x return,
                            per holding-period), and the same aggregated by sector.
  • Brinson decomposition — splits active return vs a benchmark into ALLOCATION
                            (did sector tilts help?), SELECTION (did stock picks
                            within sectors help?), and INTERACTION.

HONEST BENCHMARK: we do NOT have official NIFTY index sector weights, so the
Brinson benchmark is the **equal-weight universe** (every name with a valid price
that period, equal-weighted) — a defensible, clearly-labelled reference, not the
cap-weighted index. Contributions are gross arithmetic (sum of per-period
equal-weight portfolio returns); they approximate, and do not include costs, so
they won't equal the geometric, post-cost equity total.

Reuses the pre-registered signal logic (strategy.select), the shared rebalance
calendar, and portfolio_analyzer.SECTOR_MAP. RESEARCH ONLY — no orders.
"""

from __future__ import annotations

from collections import defaultdict

import pandas as pd

from strategy_base import MonthlyRebalanceEngine
from portfolio_analyzer import SECTOR_MAP

_ENGINE = MonthlyRebalanceEngine()


def _periods(panel: pd.DataFrame, warmup_pos: int):
    """Consecutive (rebalance_start, rebalance_end) date pairs."""
    rebals = _ENGINE.rebalance_dates(panel, warmup_pos)
    return list(zip(rebals[:-1], rebals[1:]))


def _ret(panel_val, sym, d0, d1):
    p0, p1 = panel_val.loc[d0, sym], panel_val.loc[d1, sym]
    if pd.notna(p0) and pd.notna(p1) and p0 > 0:
        return float(p1 / p0 - 1.0)
    return None


def holding_contributions(strategy, panel: pd.DataFrame) -> dict:
    """Each holding's summed (equal-weight x period-return) contribution, plus a
    sector roll-up. Total = arithmetic sum of per-period portfolio returns."""
    panel_val = panel.ffill()
    contrib, total, periods = defaultdict(float), 0.0, 0
    for d0, d1 in _periods(panel, strategy.warmup_pos):
        held = [s for s in strategy.select(panel, d0) if _ret(panel_val, s, d0, d1) is not None]
        if not held:
            continue
        w = 1.0 / len(held)
        for s in held:
            c = w * _ret(panel_val, s, d0, d1)
            contrib[s] += c
            total += c
        periods += 1
    by_sector = defaultdict(float)
    for s, c in contrib.items():
        by_sector[SECTOR_MAP.get(s, "Other")] += c
    return {"by_symbol": dict(contrib), "by_sector": dict(by_sector),
            "total": total, "periods": periods}


def brinson(strategy, panel: pd.DataFrame) -> dict:
    """Brinson-Hood-Beebower allocation / selection / interaction vs an
    equal-weight-universe benchmark. The three effects sum to active return."""
    panel_val = panel.ffill()
    agg = defaultdict(lambda: {"allocation": 0.0, "selection": 0.0, "interaction": 0.0})
    tot_port = tot_bench = 0.0
    periods = 0

    for d0, d1 in _periods(panel, strategy.warmup_pos):
        uni = [s for s in panel.columns if _ret(panel_val, s, d0, d1) is not None]
        held = [s for s in strategy.select(panel, d0) if s in uni]
        if not uni or not held:
            continue
        r = {s: _ret(panel_val, s, d0, d1) for s in uni}
        rb_total = sum(r.values()) / len(uni)

        uni_by_sec, held_by_sec = defaultdict(list), defaultdict(list)
        for s in uni:
            uni_by_sec[SECTOR_MAP.get(s, "Other")].append(s)
        for s in held:
            held_by_sec[SECTOR_MAP.get(s, "Other")].append(s)

        rp_total = 0.0
        for sec, names in uni_by_sec.items():
            wb = len(names) / len(uni)
            rb = sum(r[s] for s in names) / len(names)
            hs = held_by_sec.get(sec, [])
            wp = len(hs) / len(held)
            rp = (sum(r[s] for s in hs) / len(hs)) if hs else 0.0
            agg[sec]["allocation"] += (wp - wb) * (rb - rb_total)
            agg[sec]["selection"] += wb * (rp - rb)
            agg[sec]["interaction"] += (wp - wb) * (rp - rb)
            rp_total += wp * rp
        tot_port += rp_total
        tot_bench += rb_total
        periods += 1

    total = {k: sum(s[k] for s in agg.values())
             for k in ("allocation", "selection", "interaction")}
    total["active_return"] = tot_port - tot_bench
    return {"by_sector": {k: dict(v) for k, v in agg.items()}, "total": total,
            "portfolio_return": tot_port, "benchmark_return": tot_bench,
            "periods": periods}


def attribute(strategy, panel: pd.DataFrame) -> dict:
    return {"holdings": holding_contributions(strategy, panel),
            "brinson": brinson(strategy, panel)}
