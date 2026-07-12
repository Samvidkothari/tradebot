"""
varma_riskstate.py — Graded risk-state exposure sizer (Research/paper overlay).

Encodes the risk doctrine of Dr. Samir Varma (physicist-turned-futures-trader)
into a mechanical, auditable sizing rule. Three of his tenets drive it:

  1. **Classify, don't predict.** Do not forecast returns. Read the market's
     *current* risk STATE (high vs low) from observable, explainable measures and
     react. This overlay never predicts direction; it only scales existing
     exposure up to — never above — 1.0.
  2. **Position sizing IS risk management** — informed by Kelly but heavily
     de-risked (a fraction of Kelly). Exposure falls smoothly as risk rises,
     rather than the market-timing on/off switch that whipsaws.
  3. **Returns are leptokurtic** (fat tails; standard deviation understates the
     left tail). So a *relative* vol-regime read (percentile within its own
     trailing year) plus a hard brake in the extreme-tail state, not a Gaussian
     sigma band.

Relationship to `regime_overlay.py` (SPEC_lowvol_regime_overlay.md):
    The existing overlay is BINARY — 1.00 normally, 0.50 only in bear + 85th-pctl
    vol. This module is its **graded strict generalization**: a continuous factor
    in [FLOOR, 1.0] that (a) is <= the binary overlay in that same extreme state
    (the leptokurtic brake caps it at STRESS_CAP=0.50), and (b) also trims exposure
    gently as risk builds *before* the binary switch would ever fire. It is a
    separate, pre-registered candidate (strategies/SPEC_varma_riskstate.md); it
    does NOT modify or replace the live overlay.

Contract mirrors `regime_overlay.exposure_factor` so the two are drop-in
comparable in a shadow book:

    exposure_factor(nifty_closes, breadth_panel=None) -> dict with keys
        factor, stress, reason, regime, risk_score, components

PAPER / RESEARCH ONLY. No orders, no I/O, no data fetching. Never raises — any
data problem fails safe to a conservative neutral factor. Parameters are LOCKED
(pre-registered); they may not be tuned to results.
"""

from __future__ import annotations

from regime import classify, breadth as breadth_read, BULL, BEAR, SIDEWAYS

# ── Locked parameters (SPEC_varma_riskstate.md — DO NOT tune to results) ──────
# Risk-state weights (renormalized when breadth is unavailable).
W_TREND        = 0.50    # trend axis weight
W_VOL          = 0.40    # relative vol-regime (leptokurtic tail proxy) weight
W_BREADTH      = 0.10    # participation weight (optional; needs the close panel)

# Qualitative axis -> risk contribution in [0, 1] (higher = more risk).
TREND_RISK     = {BULL: 0.0, SIDEWAYS: 0.5, BEAR: 1.0}
BREADTH_RISK   = {"broad": 0.0, "mixed": 0.5, "narrow": 1.0}

# Exposure mapping: graded, de-risked (fractional-Kelly spirit).
FLOOR          = 0.40    # minimum exposure — Varma sizes down, never fully to cash
GRID           = 0.05    # round the factor to this grid for operational stability

# Leptokurtic tail brake — the one hard cap, matching the live overlay's trigger
# so this sizer is a strict generalization (<=) in that exact state.
EXTREME_VOL_PCTL = 0.85  # NIFTY 20d vol at/above this pctl of its trailing year
STRESS_CAP       = 0.50  # in bear + extreme vol, cap exposure at this

# Fail-safe when the risk state cannot be read: assume ELEVATED risk (Varma:
# absence of a clean low-risk signal is itself a reason to carry less), not full
# size. Conservative, and documented.
NEUTRAL_FACTOR   = 0.75
# ─────────────────────────────────────────────────────────────────────────────


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _snap(x: float) -> float:
    """Round to the operational grid, then clamp into [FLOOR, 1.0]."""
    x = round(x / GRID) * GRID
    return round(min(1.0, max(FLOOR, x)), 4)


def risk_score(trend, vol_pctl, breadth_label=None) -> tuple[float, dict]:
    """Blend the qualitative axes into a single risk state in [0, 1].

    Higher = more dangerous for a long-equity book. `trend` is the classify()
    trend axis; `vol_pctl` is vol_percentile_1y (0..1); `breadth_label` is the
    optional participation read ('broad'/'mixed'/'narrow'). When breadth is
    absent its weight is redistributed to trend and vol (so the score stays a
    proper weighted average and the module works index-only)."""
    t = TREND_RISK.get(trend)
    v = None if vol_pctl is None else _clamp01(float(vol_pctl))
    if t is None or v is None:
        return None, {"trend_risk": t, "vol_risk": v, "breadth_risk": None}

    b = BREADTH_RISK.get(breadth_label) if breadth_label is not None else None
    if b is None:                                   # index-only: renormalize
        wt = W_TREND + W_VOL
        score = (W_TREND * t + W_VOL * v) / wt
    else:
        score = W_TREND * t + W_VOL * v + W_BREADTH * b   # weights already sum ~1
    return _clamp01(score), {"trend_risk": t, "vol_risk": round(v, 3),
                             "breadth_risk": b}


def _factor_from_score(score: float) -> float:
    """Graded de-risk: linear map risk-state -> exposure in [FLOOR, 1.0].
    score 0 (calm) -> 1.0 ; score 1 (max risk) -> FLOOR."""
    return FLOOR + (1.0 - FLOOR) * (1.0 - score)


def exposure_factor(nifty_closes, breadth_panel=None) -> dict:
    """Graded exposure factor in [FLOOR, 1.0] from the current risk state.

    `nifty_closes` : pd.Series of NIFTY closes (index-level risk read).
    `breadth_panel`: optional close PANEL (all names) for a participation read;
                     omit it and the sizer runs index-only.

    Never raises. Never returns > 1.0. Returns a dict:
        factor      — the sizing multiplier to apply to each target position
        stress      — True iff the leptokurtic tail brake is binding
        reason      — human-readable explanation for the audit log
        regime      — raw classify() output (or None)
        risk_score  — blended risk state in [0, 1] (or None on fail-safe)
        components   — per-axis risk contributions
    """
    try:
        reg = classify(nifty_closes)
    except Exception as e:                                       # fail-safe
        return {"factor": NEUTRAL_FACTOR, "stress": False,
                "reason": f"risk read failed ({e}); fail-safe {NEUTRAL_FACTOR:.0%}",
                "regime": None, "risk_score": None, "components": {}}

    measures = reg.get("measures") or {}
    trend = reg.get("trend")
    vol_pctl = measures.get("vol_percentile_1y")

    # Optional participation read (never fatal).
    breadth_label = None
    if breadth_panel is not None:
        try:
            breadth_label = breadth_read(breadth_panel).get("label")
        except Exception:
            breadth_label = None

    score, components = risk_score(trend, vol_pctl, breadth_label)
    if score is None:
        return {"factor": NEUTRAL_FACTOR, "stress": False,
                "reason": ("insufficient NIFTY history for a risk state; "
                           f"fail-safe {NEUTRAL_FACTOR:.0%}"),
                "regime": reg, "risk_score": None, "components": components}

    factor = _factor_from_score(score)

    # Leptokurtic tail brake: in the extreme left-tail regime, cap hard.
    stress = (trend == BEAR and vol_pctl is not None
              and vol_pctl >= EXTREME_VOL_PCTL)
    if stress:
        factor = min(factor, STRESS_CAP)

    factor = _snap(factor)

    bpart = f", breadth {breadth_label}" if breadth_label else ""
    if stress:
        reason = (f"TAIL BRAKE — bear trend and 20d vol at {int(vol_pctl*100)}th "
                  f"pctl (>= {int(EXTREME_VOL_PCTL*100)}th): risk {score:.2f}, "
                  f"exposure capped at {factor:.0%}{bpart}")
    else:
        reason = (f"risk state {score:.2f} (trend {trend}, 20d vol "
                  f"{int((vol_pctl or 0)*100)}th pctl{bpart}) -> exposure "
                  f"{factor:.0%}")

    return {"factor": factor, "stress": stress, "reason": reason,
            "regime": reg, "risk_score": round(score, 4), "components": components}


# ── Manual research demo (never runs on import) ───────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import sys
    import pandas as pd

    df = pd.read_csv("data/NIFTY50.csv")
    s = pd.Series(df["close"].values,
                  index=pd.to_datetime(df["date"])).sort_index()

    # Walk the tail of history, printing the factor each month-end, so a human can
    # eyeball that it de-risks into known drawdowns and never exceeds 1.0.
    print(f"{'date':<12}{'trend':<10}{'volpctl':>8}{'risk':>7}{'factor':>8}  note")
    monthly = s.resample("ME").last().dropna().index
    for d in monthly[-24:]:
        r = exposure_factor(s[s.index <= d])
        m = (r["regime"] or {}).get("measures", {})
        vp = m.get("vol_percentile_1y")
        trend = str((r["regime"] or {}).get("trend"))
        vp_s = "" if vp is None else f"{vp:.2f}"
        rs_s = "" if r["risk_score"] is None else f"{r['risk_score']:.2f}"
        note = "STRESS" if r["stress"] else ""
        print(f"{str(d.date()):<12}{trend:<10}{vp_s:>8}{rs_s:>7}"
              f"{r['factor']:>8.2f}  {note}")
    latest = exposure_factor(s)
    print("\nlatest:", latest["reason"])
    assert latest["factor"] <= 1.0, "invariant violated: factor > 1.0"
    sys.exit(0)
