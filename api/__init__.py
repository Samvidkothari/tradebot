"""
api/ — L5 JSON API layer (v1). READ-ONLY.

Decouples data assembly from HTML rendering: every endpoint returns JSON only,
consumed by the /app React frontend (and anything else). All routes are
login-protected and open the ledgers via read-only SQLite URIs — this package
contains no order-placement code and never writes to a ledger.

Blueprint: /api/v1/*
    overview            hero: capital, per-book P&L, totals, regime, vol flag
    books               enriched book rows (with spark series)
    books/<key>/ledger  the daily simulated ledger for one book
    risk                risk checks + the 3-tier Drawdown Thermometer
    research            per-strategy tear-sheet summary (plain-English ready)
    costs               the intraday Slippage & Cost lesson (frozen evidence)
    precommit           pre-committed options judgment criteria
    regime              market regime + the low-vol overlay's last decision
"""

from flask import Blueprint

from web_common import login_required

bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

from . import books, risk, research, meta  # noqa: E402,F401  (attach routes)


def register(app):
    """Attach the JSON API. Call from dashboard.py after web_common is ready."""
    app.register_blueprint(bp)
