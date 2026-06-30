"""
test_controls.py — the paper-only control layer (enable/disable + background jobs).

Covers the safety allow-list (only listed paper scripts can run, research-only is
refused), flag persistence, the job status machine (running → done/stopped via the
rc-file / liveness reconcile), and one real end-to-end spawn of a trivial script.
All isolated to a tmp DB + jobs dir — the real controls.db is never touched, and
no paper simulator or data script is actually launched except a harmless stub.
"""

import subprocess
import time

import pytest

import controls


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(controls, "DB_PATH", tmp_path / "controls.db")
    monkeypatch.setattr(controls, "JOBS_DIR", tmp_path / "jobs")
    return tmp_path


def test_flags_default_and_persist(iso):
    assert controls.is_enabled("lowvol") is True          # default on
    assert controls.is_enabled("momentum") is False       # research-only default off
    controls.set_enabled("lowvol", False)
    assert controls.is_enabled("lowvol") is False
    assert controls.toggle("lowvol") is True               # flips back


def test_allowlist_rejects_unknown_and_research_only(iso):
    with pytest.raises(KeyError):
        controls.start("strategy", "rm -rf /")             # not on the list
    with pytest.raises(KeyError):
        controls.set_enabled("bogus", True)
    with pytest.raises(ValueError):
        controls.start("strategy", "momentum")             # script is None → refused


def test_job_status_reconciles_to_stopped_when_pid_dead(iso):
    controls.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    dead = subprocess.Popen(["bash", "-c", "true"]); dead.wait()   # a definitely-dead pid
    conn = controls._db()
    conn.execute("INSERT INTO jobs (id,kind,key,label,pid,status,started) "
                 "VALUES ('jX','task','prices','Refresh prices',?, 'running','2026-06-30 10:00:00')",
                 (dead.pid,))
    conn.commit(); conn.close()
    js = {j["id"]: j for j in controls.jobs()}
    assert js["jX"]["status"] == "stopped"                 # no rc + pid gone


def test_job_status_reconciles_to_done_with_rc(iso):
    controls.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    (controls.JOBS_DIR / "jY.rc").write_text("0")
    conn = controls._db()
    conn.execute("INSERT INTO jobs (id,kind,key,label,pid,status,started) "
                 "VALUES ('jY','task','prices','x',999999,'running','2026-06-30 10:01:00')")
    conn.commit(); conn.close()
    js = {j["id"]: j for j in controls.jobs()}
    assert js["jY"]["status"] == "done" and js["jY"]["rc"] == 0


def test_start_spawns_records_and_completes(iso, monkeypatch):
    # Point the resolver at a harmless stub so we exercise the real spawn/record/
    # reconcile path without launching a paper simulator.
    script = iso / "noop.py"
    script.write_text("print('hello from job')\n")
    monkeypatch.setattr(controls, "_resolve", lambda kind, key: ("Noop", script))

    job = controls.start("task", "prices")
    assert job["status"] == "running" and job["pid"]

    final = None
    for _ in range(50):                                    # ≤5s
        final = {j["id"]: j for j in controls.jobs()}[job["id"]]
        if final["status"] != "running":
            break
        time.sleep(0.1)
    assert final["status"] == "done" and final["rc"] == 0
    assert "hello from job" in controls.log_tail(job["id"])


def test_stop_unknown_job_is_falsey(iso):
    assert controls.stop("nope") is False
