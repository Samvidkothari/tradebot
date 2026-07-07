"""api/meta.py — costs lesson, pre-committed criteria, regime + overlay (read-only)."""

import json
import sqlite3

from flask import jsonify

from web_common import BASE_DIR, login_required, paper_db, ro_db
import views_research as vr

from . import bp


@bp.get("/costs")
@login_required
def api_costs():
    lesson = vr._cost_lesson()
    if lesson is None:
        return jsonify({"available": False})
    return jsonify({
        "available": True, **lesson,
        "story": ("These two day-trading bots found a real edge — and fees ate "
                  "all of it and more. They're retired forever; this card is "
                  "the receipt, so the lesson never gets re-learned with real "
                  "money."),
    })


@bp.get("/precommit")
@login_required
def api_precommit():
    out = {}
    for key, db in (("strangle", "options.db"), ("condor", "condor.db")):
        conn = ro_db(BASE_DIR / db)
        if conn is None:
            continue
        try:
            out[key] = {r["key"]: {"value": r["value"], "committed": r["committed"]}
                        for r in conn.execute(
                            "SELECT key, value, committed FROM precommit")}
        except sqlite3.Error:      # table appears after the sims' next run
            out[key] = {}
        finally:
            conn.close()
    return jsonify({"criteria": out, "vol_event": vr._vol_event_status(),
                    "note": "Written once, never updated — the verdict can't "
                            "be argued after the fact."})


@bp.get("/regime")
@login_required
def api_regime():
    ts, _ = vr._research_json("tearsheets.json", "tearsheet.py")
    overlay = None
    conn = paper_db()
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_regime_overlay'"
            ).fetchone()
            if row:
                overlay = json.loads(row["value"])
        except (sqlite3.Error, ValueError):
            pass
        finally:
            conn.close()
    return jsonify({"regime": (ts or {}).get("regime"), "overlay": overlay})
