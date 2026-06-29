"""
features package — extended dashboard features (analytics, journal, alerts,
simulated order ticket, exports).

Split out of the original single features.py for maintainability. All routes
register on ONE blueprint named "features" (defined in .core), so existing
`from features import bp` and every `url_for('features.…')` keep working
unchanged. Importing the route submodules below is what attaches their routes.
"""

from .core import bp, strategy_analytics, intraday_strategies  # public surface

# Importing these registers their @bp.route handlers on `bp`.
from . import analytics, journal, alerts, ticket, exports   # noqa: E402,F401

__all__ = ["bp", "strategy_analytics", "intraday_strategies"]
