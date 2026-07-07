"""
controls.py — paper-only strategy & data controls for the command UI.

Lets the local dashboard (a) enable/disable a paper strategy, (b) trigger a
single paper-sim run, and (c) refresh data — each as a tracked background job.

SAFETY: this is strictly paper / research. Every runnable target is an
ALLOW-LISTED simulation or data script (see STRATEGIES / TASKS); an unknown key
is rejected. There is NO order-placement path here and none is added — "Run" only
ever launches one of the existing paper simulators or the data/research scripts,
which write to the local paper ledgers and caches, never to a broker.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import jsonify

from web_common import BASE_DIR, rw_db, ro_db

JOBS_DIR = BASE_DIR / "jobs"
# Overridable via CONTROLS_DB (used by tests / alternate schedulers).
DB_PATH = Path(os.environ.get("CONTROLS_DB") or (BASE_DIR / "controls.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT, key TEXT, label TEXT, pid INTEGER,
    status TEXT, started TEXT, finished TEXT, rc INTEGER
);
"""

# ── Allow-list: the ONLY things the UI is permitted to launch ─────────────────
# Each value is a paper simulator or a data/research script. `script: None` means
# the strategy is research-only and cannot be run on demand.
STRATEGIES = {
    "lowvol":   {"label": "Low-Volatility",    "script": "paper_trader.py",     "default": True},
    "strangle": {"label": "Options strangle",  "script": "options_sim.py",      "default": True},
    "condor":   {"label": "Options condor",    "script": "condor_sim.py",       "default": True},
    "intraday": {"label": "Intraday ORB+VWAP", "script": "intraday_sim.py",     "default": True},
    "momentum": {"label": "Momentum 12-1",     "script": "backtest_momentum.py", "default": True},
    "llm":      {"label": "LLM analyst",       "script": "llm_analyst.py",      "default": True},
}
TASKS = {
    "prices":   {"label": "Refresh prices",    "script": "fetch_data.py"},
    "pipeline": {"label": "Run full pipeline", "script": "research_pipeline.py"},
}
# Settle = close the open options position now at its current mark (--settle).
SETTLE = {
    "strangle": {"label": "Settle strangle", "script": "options_sim.py", "args": ["--settle"]},
    "condor":   {"label": "Settle condor",   "script": "condor_sim.py",  "args": ["--settle"]},
}


def _db():
    JOBS_DIR.mkdir(exist_ok=True)
    return rw_db(DB_PATH, _SCHEMA)


# ── Enable / disable flags ────────────────────────────────────────────────────

def is_enabled(key: str) -> bool:
    default = STRATEGIES.get(key, {}).get("default", True)
    try:
        conn = _db()
    except Exception:
        return default                      # degrade if the DB is unavailable
    try:
        row = conn.execute("SELECT enabled FROM flags WHERE key = ?", (key,)).fetchone()
        return bool(row["enabled"]) if row else default
    except Exception:
        return default
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_enabled(key: str, enabled: bool) -> bool:
    if key not in STRATEGIES:
        raise KeyError(key)
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO flags (key, enabled) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET enabled = excluded.enabled",
            (key, 1 if enabled else 0))
        conn.commit()
    finally:
        conn.close()
    return enabled


def toggle(key: str) -> bool:
    new = not is_enabled(key)
    return set_enabled(key, new)


def flags() -> dict:
    return {k: is_enabled(k) for k in STRATEGIES}


# ── Background jobs (allow-listed scripts only) ───────────────────────────────

def _resolve(kind: str, key: str):
    table = (STRATEGIES if kind == "strategy" else TASKS if kind == "task"
             else SETTLE if kind == "settle" else None)
    if table is None or key not in table:
        raise KeyError(f"unknown {kind}: {key}")
    script = table[key].get("script")
    if not script:
        raise ValueError(f"{key} is research-only and cannot be run")
    path = (BASE_DIR / script).resolve()
    # Hard guard: the resolved script must live in BASE_DIR and be allow-listed.
    if path.parent != BASE_DIR.resolve() or not path.exists():
        raise ValueError(f"refusing to run {script}")
    return table[key]["label"], path, list(table[key].get("args", []))


def start(kind: str, key: str) -> dict:
    """Launch an allow-listed paper sim / data script as a detached background
    job. Returns the job record. Raises KeyError/ValueError for anything not on
    the allow-list."""
    label, path, extra = _resolve(kind, key)
    jid = uuid.uuid4().hex[:12]
    log = JOBS_DIR / f"{jid}.log"
    rc = JOBS_DIR / f"{jid}.rc"
    argv = [sys.executable, str(path), *extra]
    # Wrapper writes the return code on completion so status survives restarts.
    wrapped = (f"{_q(argv)} > {_q([str(log)])} 2>&1; "
               f"printf %s $? > {_q([str(rc)])}")
    p = subprocess.Popen(["bash", "-lc", wrapped], cwd=str(BASE_DIR),
                         start_new_session=True)
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO jobs (id, kind, key, label, pid, status, started) "
            "VALUES (?,?,?,?,?, 'running', ?)",
            (jid, kind, key, label, p.pid, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    return {"id": jid, "label": label, "status": "running", "pid": p.pid}


def _q(parts):
    import shlex
    return " ".join(shlex.quote(x) for x in parts)


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _reconcile(conn, row):
    """Update a 'running' job that has actually finished (rc file present, or the
    pid is gone). Returns the up-to-date status dict."""
    jid, pid, status = row["id"], row["pid"], row["status"]
    rc_val, finished = row["rc"], row["finished"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rcf = JOBS_DIR / f"{jid}.rc"
    if status == "running":
        if rcf.exists():
            try:
                rc_val = int(rcf.read_text().strip() or "0")
            except ValueError:
                rc_val = 0
            status = "done" if rc_val == 0 else "failed"
            finished = now
            conn.execute("UPDATE jobs SET status=?, rc=?, finished=? WHERE id=?",
                         (status, rc_val, finished, jid))
            conn.commit()
        elif not _alive(pid):
            status, finished = "stopped", now
            conn.execute("UPDATE jobs SET status=?, finished=? WHERE id=?",
                         (status, finished, jid))
            conn.commit()
    return {"id": jid, "kind": row["kind"], "key": row["key"], "label": row["label"],
            "status": status, "started": row["started"], "finished": finished,
            "rc": rc_val}


def jobs(limit: int = 12) -> list:
    try:
        conn = _db()
    except Exception:
        return []                           # degrade if the DB is unavailable
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY started DESC LIMIT ?",
                            (limit,)).fetchall()
        return [_reconcile(conn, r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def stop(jid: str) -> bool:
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        if row is None:
            return False
        if row["status"] == "running" and _alive(row["pid"]):
            try:
                os.killpg(os.getpgid(row["pid"]), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        conn.execute("UPDATE jobs SET status='stopped', finished=? WHERE id=?",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), jid))
        conn.commit()
        return True
    finally:
        conn.close()


def log_tail(jid: str, n: int = 40) -> str:
    log = JOBS_DIR / f"{jid}.log"
    if not log.exists():
        return ""
    return "\n".join(log.read_text(errors="replace").splitlines()[-n:])


# ── Flask wiring ──────────────────────────────────────────────────────────────

def register_controls(app, guard):
    """Attach the control routes (POST actions + a jobs poll). `guard` is the
    login_required decorator from web_common, so every control is auth-gated."""

    def run_strategy(key):
        try:
            return jsonify(ok=True, job=start("strategy", key))
        except (KeyError, ValueError) as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            return jsonify(ok=False, error=f"could not start: {e}"), 500

    def run_task(key):
        try:
            return jsonify(ok=True, job=start("task", key))
        except (KeyError, ValueError) as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            return jsonify(ok=False, error=f"could not start: {e}"), 500

    def settle_book(key):
        try:
            return jsonify(ok=True, job=start("settle", key))
        except (KeyError, ValueError) as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            return jsonify(ok=False, error=f"could not settle: {e}"), 500

    def toggle_strategy(key):
        try:
            return jsonify(ok=True, enabled=toggle(key))
        except KeyError as e:
            return jsonify(ok=False, error=str(e)), 400
        except Exception as e:
            return jsonify(ok=False, error=f"could not toggle: {e}"), 500

    def stop_job(jid):
        try:
            return jsonify(ok=stop(jid))
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500

    def jobs_json():
        return jsonify(jobs=jobs(), flags=flags())

    rules = [
        ("/command/control/run/<key>", "ctl_run", run_strategy, ["POST"]),
        ("/command/control/task/<key>", "ctl_task", run_task, ["POST"]),
        ("/command/control/settle/<key>", "ctl_settle", settle_book, ["POST"]),
        ("/command/control/toggle/<key>", "ctl_toggle", toggle_strategy, ["POST"]),
        ("/command/control/stop/<jid>", "ctl_stop", stop_job, ["POST"]),
        ("/command/control/jobs.json", "ctl_jobs", jobs_json, ["GET"]),
    ]
    for path, endpoint, fn, methods in rules:
        app.add_url_rule(path, endpoint, guard(fn), methods=methods)


# ── CLI: let the scheduled bot honour the enable/disable flags ────────────────
# Usage:  python controls.py is-enabled <key>   → exit 0 if enabled, 1 if disabled
# So run_paper_bot.sh can skip a book the user switched off in the dashboard.
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "is-enabled":
        sys.exit(0 if is_enabled(sys.argv[2]) else 1)
    print("usage: python controls.py is-enabled <strategy-key>", file=sys.stderr)
    sys.exit(2)
