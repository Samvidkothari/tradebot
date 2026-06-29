"""features/journal.py — trade journal (writes to the isolated journal.db only)."""

from datetime import datetime

from flask import redirect, render_template, request, url_for

from .core import (bp, login_required, _rw, JOURNAL_DB, JOURNAL_SCHEMA,
                   intraday_strategies)


@bp.route("/journal")
@login_required
def journal():
    c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
    try:
        entries = [dict(r) for r in c.execute(
            "SELECT * FROM entries ORDER BY id DESC LIMIT 200")]
        n = len(entries)
        avg_rating = (sum(e["rating"] for e in entries if e["rating"]) /
                      max(1, sum(1 for e in entries if e["rating"]))) if entries else None
    finally:
        c.close()
    books = intraday_strategies() + ["lowvol", "options", "condor", "live", "other"]
    return render_template("journal.html", active="journal",
                           entries=entries, n=n, avg_rating=avg_rating, books=books)


@bp.route("/journal/add", methods=["POST"])
@login_required
def journal_add():
    f = request.form
    title = (f.get("title") or "").strip()
    note = (f.get("note") or "").strip()
    if not title and not note:
        return redirect(url_for("features.journal"))
    try:
        rating = int(f.get("rating") or 0) or None
    except ValueError:
        rating = None
    c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
    try:
        c.execute(
            "INSERT INTO entries (created, book, symbol, side, tag, rating, title, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M"),
             (f.get("book") or "").strip(), (f.get("symbol") or "").strip().upper(),
             (f.get("side") or "").strip(), (f.get("tag") or "").strip(),
             rating, title, note))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.journal"))


@bp.route("/journal/<int:entry_id>/delete", methods=["POST"])
@login_required
def journal_delete(entry_id):
    c = _rw(JOURNAL_DB, JOURNAL_SCHEMA)
    try:
        c.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.journal"))
