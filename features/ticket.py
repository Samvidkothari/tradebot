"""
features/ticket.py — SIMULATED, paper-only order ticket.

Submitting records a row in orders.db with status 'SIMULATED' and stops there.
There is no code path that contacts a broker or places a real order; the
`# LIVE EXECUTION HOOK` marker shows where one would go if ever wired, behind an
explicit per-order confirmation and a hard live-trading flag — intentionally
left unimplemented.
"""

from datetime import datetime

from flask import redirect, render_template, request, url_for

from .core import (bp, login_required, _ro, _rw, ORDERS_DB, ORDERS_SCHEMA,
                   PORTFOLIO_DB)


@bp.route("/ticket")
@login_required
def ticket():
    c = _rw(ORDERS_DB, ORDERS_SCHEMA)
    try:
        orders = [dict(r) for r in c.execute(
            "SELECT * FROM orders ORDER BY id DESC LIMIT 100")]
    finally:
        c.close()
    symbols = []
    pc = _ro(PORTFOLIO_DB)
    if pc is not None:
        try:
            symbols = [r["symbol"] for r in
                       pc.execute("SELECT symbol FROM positions ORDER BY symbol")]
        finally:
            pc.close()
    return render_template("ticket.html", active="ticket", orders=orders, symbols=symbols)


@bp.route("/ticket/submit", methods=["POST"])
@login_required
def ticket_submit():
    f = request.form
    symbol = (f.get("symbol") or "").strip().upper()
    side = f.get("side")
    order_type = f.get("order_type") or "MARKET"
    try:
        qty = int(f.get("qty"))
    except (TypeError, ValueError):
        qty = 0
    if not symbol or side not in ("BUY", "SELL") or qty <= 0:
        return redirect(url_for("features.ticket"))
    try:
        limit_price = float(f.get("limit_price")) if f.get("limit_price") else None
    except ValueError:
        limit_price = None

    # ── This is the safety boundary. We persist a SIMULATED row and stop. ──
    c = _rw(ORDERS_DB, ORDERS_SCHEMA)
    try:
        c.execute(
            "INSERT INTO orders (created, symbol, side, qty, order_type, limit_price, note, mode, status) "
            "VALUES (?,?,?,?,?,?,?, 'PAPER', 'SIMULATED')",
            (datetime.now().strftime("%Y-%m-%d %H:%M"), symbol, side, qty,
             order_type, limit_price, (f.get("note") or "").strip()))
        c.commit()
    finally:
        c.close()
    # LIVE EXECUTION HOOK — intentionally NOT implemented.
    # A real integration would place the broker order here only after explicit,
    # per-order human confirmation and a hard live-trading enable flag.
    return redirect(url_for("features.ticket"))


@bp.route("/ticket/<int:order_id>/delete", methods=["POST"])
@login_required
def ticket_delete(order_id):
    c = _rw(ORDERS_DB, ORDERS_SCHEMA)
    try:
        c.execute("DELETE FROM orders WHERE id=?", (order_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.ticket"))
