"""features/analytics.py — risk/return analytics page (read-only)."""

from flask import render_template, request

from web_common import BOOK_STATUS, status_for, sparkline_svg
from .core import bp, login_required, intraday_strategies, strategy_analytics


@bp.route("/analytics")
@login_required
def analytics():
    strategies = intraday_strategies()
    if not strategies:
        return render_template("analytics.html", active="analytics",
                               error="No intraday data yet — run the paper bot first.",
                               strategies=[], selected=None, a=None, chart=None)
    selected = request.args.get("strategy")
    if selected not in strategies:
        selected = strategies[0]
    a = strategy_analytics(selected)
    chart = None
    if a and a["dates"]:
        chart = {"labels": a["dates"],
                 "equity": a["equity_curve"],
                 "drawdown": a["drawdown_curve"]}
        a["equity_spark"] = sparkline_svg(a["equity_curve"], width=120, height=28)
        a["status"] = BOOK_STATUS.get(status_for(None, selected), BOOK_STATUS["paper"])
    return render_template("analytics.html", active="analytics", error=None,
                           strategies=strategies, selected=selected, a=a, chart=chart)
