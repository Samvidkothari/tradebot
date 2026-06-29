"""features/exports.py — CSV downloads + printable performance report."""

from datetime import datetime

from flask import abort, render_template, request

from .core import (bp, login_required, _ro, _rw, _csv_response, STARTING_CAPITAL,
                   PORTFOLIO_DB, INTRADAY_DB, JOURNAL_DB, JOURNAL_SCHEMA,
                   intraday_strategies, strategy_analytics, _paper_equity_and_realised)


@bp.route("/export/<kind>.csv")
@login_required
def export_csv(kind):
    if kind == "paper_positions":
        c = _ro(PORTFOLIO_DB)
        if c is None:
            abort(404)
        try:
            rows = [(r["symbol"], r["qty"], r["avg_price"], r["opened"]) for r in
                    c.execute("SELECT symbol, qty, avg_price, opened FROM positions ORDER BY symbol")]
        finally:
            c.close()
        return _csv_response("paper_positions.csv",
                             ["symbol", "qty", "avg_price", "opened"], rows)

    if kind == "paper_fills":
        c = _ro(PORTFOLIO_DB)
        if c is None:
            abort(404)
        try:
            rows = [(r["run_date"], r["symbol"], r["side"], r["qty"], r["price"],
                     r["cost"], r["realised_pnl"]) for r in c.execute(
                "SELECT run_date, symbol, side, qty, price, cost, realised_pnl "
                "FROM fills ORDER BY id")]
        finally:
            c.close()
        return _csv_response("paper_fills.csv",
                             ["run_date", "symbol", "side", "qty", "price", "cost", "realised_pnl"], rows)

    if kind == "intraday_trades":
        c = _ro(INTRADAY_DB)
        if c is None:
            abort(404)
        strat = request.args.get("strategy")
        try:
            if strat:
                cur = c.execute(
                    "SELECT trade_date, strategy, symbol, side, entry_time, entry_px, "
                    "exit_time, exit_px, qty, gross_pnl, costs, net_pnl, exit_reason "
                    "FROM trades WHERE strategy=? ORDER BY id", (strat,))
            else:
                cur = c.execute(
                    "SELECT trade_date, strategy, symbol, side, entry_time, entry_px, "
                    "exit_time, exit_px, qty, gross_pnl, costs, net_pnl, exit_reason "
                    "FROM trades ORDER BY id")
            rows = [tuple(r) for r in cur]
        finally:
            c.close()
        return _csv_response("intraday_trades.csv",
                             ["trade_date", "strategy", "symbol", "side", "entry_time",
                              "entry_px", "exit_time", "exit_px", "qty", "gross_pnl",
                              "costs", "net_pnl", "exit_reason"], rows)

    if kind == "journal":
        c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
        try:
            rows = [(r["created"], r["book"], r["symbol"], r["side"], r["tag"],
                     r["rating"], r["title"], r["note"]) for r in
                    c.execute("SELECT * FROM entries ORDER BY id")]
        finally:
            c.close()
        return _csv_response("journal.csv",
                             ["created", "book", "symbol", "side", "tag", "rating", "title", "note"], rows)

    abort(404)


@bp.route("/report")
@login_required
def report():
    strategies = intraday_strategies()
    books = [strategy_analytics(s) for s in strategies]
    books = [b for b in books if b]
    equity, realised = _paper_equity_and_realised()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return render_template("report.html", books=books, generated=generated,
                           paper_equity=equity, paper_realised=realised,
                           starting=STARTING_CAPITAL)
