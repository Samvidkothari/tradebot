"""api/risk.py — risk checks + the 3-tier Drawdown Thermometer (read-only)."""

from flask import jsonify

from web_common import login_required
import views_research as vr

from . import bp

# Thermometer tiers (fraction of the hard drawdown limit consumed).
# Plain-English by design: the 15-year-old reads a colour, not a formula.
TIER_MINT_MAX  = 0.40   # < 40% of the limit used  → mint  ("All chill")
TIER_AMBER_MAX = 0.75   # < 75%                    → amber ("Getting warm")
                        # >= 75%                   → crimson ("Danger zone")

TIER_LABELS = {
    "mint":    {"label": "All chill",    "color": "#3ddc97",
                "story": "The dip is small. Nothing to do."},
    "amber":   {"label": "Getting warm", "color": "#e3b341",
                "story": "The dip is getting deeper. The bot sizes down in "
                         "stress — watch, don't panic."},
    "crimson": {"label": "Danger zone",  "color": "#e5484d",
                "story": "Close to the pre-set safety line. If it crosses, "
                         "the rules say stop and review — no heroics."},
}


def thermometer(checks):
    """3-tier drawdown read from the risk engine's max_drawdown check."""
    dd = (checks or {}).get("max_drawdown") or {}
    value, limit = dd.get("value"), dd.get("limit")
    if value is None or not limit:
        return {"tier": "mint", "used_pct": 0, **TIER_LABELS["mint"],
                "drawdown_pct": None, "limit_pct": None}
    used = min(abs(value) / abs(limit), 1.0)
    tier = ("mint" if used < TIER_MINT_MAX
            else "amber" if used < TIER_AMBER_MAX else "crimson")
    return {"tier": tier, "used_pct": round(used * 100, 1),
            "drawdown_pct": round(value * 100, 2),
            "limit_pct": round(limit * 100, 1), **TIER_LABELS[tier]}


@bp.get("/risk")
@login_required
def api_risk():
    re_, _ = vr._research_json("risk_engine.json", "risk_engine.py")
    rk, _ = vr._research_json("risk.json", "risk_report.py")
    checks = (re_ or {}).get("checks") or {}
    return jsonify({
        "status": (re_ or {}).get("status", "—"),
        "emergency": bool((re_ or {}).get("emergency")),
        "reason": (re_ or {}).get("reason", ""),
        "as_of": (re_ or {}).get("as_of"),
        "checks": checks,
        "thermometer": thermometer(checks),
        "rows": vr._command_risk_rows(rk, re_),
    })
