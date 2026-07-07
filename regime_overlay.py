"""
regime_overlay.py — Pre-registered defensive sizing overlay for the LIVE
low-vol paper book (strategies/SPEC_lowvol_regime_overlay.md).

On a rebalance day, paper_trader asks this module for an exposure factor:

    1.00  normal — full size (equity / TOP_N per name)
    0.50  extreme stress — bear trend AND 20d NIFTY vol >= 85th pctl of its year

The regime read is regime.classify (unchanged, research classifier). Any data
problem fails safe to 1.00 — the overlay can only stand aside, never block the
strategy or increase exposure. Parameters are LOCKED (no re-tuning to results).

Paper only. No orders. The pre-registered lowvol backtest is untouched.
"""

from __future__ import annotations

from regime import classify, BEAR

# ── Locked parameters (SPEC_lowvol_regime_overlay.md — do not tune) ───────────
STRESS_VOL_PCTL = 0.85   # NIFTY 20d vol at/above this pctl of its trailing year
STRESS_EXPOSURE = 0.50   # sizing factor under stress
NORMAL_EXPOSURE = 1.00
# ─────────────────────────────────────────────────────────────────────────────


def exposure_factor(nifty_closes) -> dict:
    """Decide the sizing factor from NIFTY closes. Never raises.

    Returns {"factor", "stress", "reason", "regime"} where regime is the raw
    classify() output (or None if unavailable)."""
    try:
        reg = classify(nifty_closes)
    except Exception as e:                                  # fail-safe: overlay off
        return {"factor": NORMAL_EXPOSURE, "stress": False,
                "reason": f"overlay inactive (regime read failed: {e})",
                "regime": None}

    measures = reg.get("measures") or {}
    pctl = measures.get("vol_percentile_1y")
    trend = reg.get("trend")

    if trend is None or pctl is None:
        return {"factor": NORMAL_EXPOSURE, "stress": False,
                "reason": "overlay inactive (insufficient NIFTY history)",
                "regime": reg}

    if trend == BEAR and pctl >= STRESS_VOL_PCTL:
        return {"factor": STRESS_EXPOSURE, "stress": True,
                "reason": (f"EXTREME STRESS — bear trend and 20d vol at "
                           f"{int(pctl * 100)}th pctl (>= {int(STRESS_VOL_PCTL * 100)}th): "
                           f"sizing at {STRESS_EXPOSURE:.0%}"),
                "regime": reg}

    return {"factor": NORMAL_EXPOSURE, "stress": False,
            "reason": (f"normal — trend {trend}, 20d vol at {int(pctl * 100)}th "
                       f"pctl (< {int(STRESS_VOL_PCTL * 100)}th needed with bear)"),
            "regime": reg}
