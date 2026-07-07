"""api/research.py — tear-sheet summaries with plain-English labels (read-only)."""

from flask import jsonify

from web_common import login_required
import views_research as vr

from . import bp

# The jargon dictionary. The frontend shows the plain term first; the real
# term lives in the tooltip so the user levels up over time.
PLAIN = {
    "cagr":          {"plain": "Yearly growth",
                      "tip": "CAGR — how much the pot grows per year on average."},
    "total_return":  {"plain": "Total growth",
                      "tip": "How much the pot has grown overall since the start."},
    "max_drawdown":  {"plain": "Biggest dip",
                      "tip": "Max drawdown — the worst peak-to-bottom fall so far."},
    "sharpe":        {"plain": "Smoothness score",
                      "tip": "Sharpe ratio — growth earned per unit of bumpiness. Higher = smoother ride."},
    "annual_vol":    {"plain": "Bumpiness",
                      "tip": "Volatility — how much the value wiggles day to day."},
    "alpha":         {"plain": "Edge vs the index",
                      "tip": "Alpha — extra return beyond just buying the NIFTY 50."},
    "win_rate":      {"plain": "Days won",
                      "tip": "Share of days the book made money."},
    "premium":       {"plain": "Rent collected",
                      "tip": "Option premium — money collected for taking the other side."},
    "theta":         {"plain": "Daily rent",
                      "tip": "Theta — the little bit an option seller earns as each quiet day passes."},
    "vol_event":     {"plain": "Storm day",
                      "tip": "A ≥4% NIFTY move in one day — the storm these option trades are waiting to be tested by."},
    "unrealised":    {"plain": "Paper gains",
                      "tip": "Profit that exists on screen but isn't locked in yet."},
    "realised":      {"plain": "Locked in",
                      "tip": "Profit (or loss) from positions already closed."},
}

FIELDS = ("cagr", "total_return", "max_drawdown", "annual_vol", "sharpe", "alpha")


@bp.get("/research")
@login_required
def api_research():
    ts, _ = vr._research_json("tearsheets.json", "tearsheet.py")
    out = []
    for s in ((ts or {}).get("strategies") or {}).values():
        full, oos = s.get("full") or {}, s.get("oos") or {}
        out.append({
            "name": s.get("name"), "label": s.get("label"),
            "kind": s.get("kind"),
            "full": {k: full.get(k) for k in FIELDS},
            "oos": {k: oos.get(k) for k in FIELDS},
            "regime_compat": s.get("regime_compat"),
        })
    return jsonify({"strategies": out, "dictionary": PLAIN,
                    "generated": (ts or {}).get("generated")})
