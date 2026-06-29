"""features/alerts.py — threshold alert rules (alerts.db), evaluated read-only."""

from datetime import datetime

from flask import redirect, render_template, request, url_for

from .core import (bp, login_required, _rw, ALERTS_DB, ALERTS_SCHEMA, ALERT_KINDS,
                   _alert_value, _triggered, intraday_strategies)


@bp.route("/alerts")
@login_required
def alerts():
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        rules = [dict(r) for r in c.execute("SELECT * FROM rules ORDER BY id DESC")]
    finally:
        c.close()

    evaluated, fired_ids = [], []
    for r in rules:
        val = _alert_value(r)
        fired = _triggered(val, r["op"], r["threshold"]) if r["active"] else None
        if fired:
            fired_ids.append(r["id"])
        evaluated.append({**r, "value": val, "fired": fired,
                          "kind_label": ALERT_KINDS.get(r["kind"], r["kind"])})

    if fired_ids:   # stamp last_triggered (the one write the alerts page makes)
        c = _rw(ALERTS_DB, ALERTS_SCHEMA)
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            c.executemany("UPDATE rules SET last_triggered=? WHERE id=?",
                          [(now, i) for i in fired_ids])
            c.commit()
        finally:
            c.close()

    n_fired = sum(1 for e in evaluated if e["fired"])
    return render_template("alerts.html", active="alerts", rules=evaluated,
                           kinds=ALERT_KINDS, strategies=intraday_strategies(),
                           n_fired=n_fired)


@bp.route("/alerts/add", methods=["POST"])
@login_required
def alerts_add():
    f = request.form
    kind = f.get("kind")
    op = f.get("op")
    if kind not in ALERT_KINDS or op not in (">=", "<="):
        return redirect(url_for("features.alerts"))
    try:
        threshold = float(f.get("threshold"))
    except (TypeError, ValueError):
        return redirect(url_for("features.alerts"))
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        c.execute(
            "INSERT INTO rules (created, kind, target, op, threshold, note, active) "
            "VALUES (?,?,?,?,?,?,1)",
            (datetime.now().strftime("%Y-%m-%d %H:%M"), kind,
             (f.get("target") or "").strip(), op, threshold,
             (f.get("note") or "").strip()))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.alerts"))


@bp.route("/alerts/<int:rule_id>/toggle", methods=["POST"])
@login_required
def alerts_toggle(rule_id):
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        c.execute("UPDATE rules SET active = 1 - active WHERE id=?", (rule_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.alerts"))


@bp.route("/alerts/<int:rule_id>/delete", methods=["POST"])
@login_required
def alerts_delete(rule_id):
    c = _rw(ALERTS_DB, ALERTS_SCHEMA)
    try:
        c.execute("DELETE FROM rules WHERE id=?", (rule_id,))
        c.commit()
    finally:
        c.close()
    return redirect(url_for("features.alerts"))
