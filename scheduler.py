"""
scheduler.py — APScheduler daemon for the paper bot (single supervised process).

Replaces "remember to run run_paper_bot.sh" with a self-scheduling daemon.
Two jobs, both in Asia/Kolkata time, both guarded by the NSE session calendar:

  daily_pipeline   15:45 IST Mon–Fri   run_paper_bot.sh — the existing full
                                        chain (fetch → options sims → research
                                        pipeline → digest → notifications).
                                        The script keeps its own session guard,
                                        so a holiday run exits cleanly.
  hourly_mark      10:15–15:15 IST     intraday_mark.main() — mark-to-market +
                   (hourly, Mon–Fri)    risk governor on 60m data. Trades
                                        nothing (see intraday_mark.py header).

Design notes:
  • coalesce=True + misfire_grace_time: if the laptop sleeps through slots,
    APScheduler runs each job ONCE on wake instead of replaying every miss.
  • max_instances=1: a slow yfinance sweep can never overlap the next slot.
  • Jobs are wrapped fail-soft — one bad run logs and waits for the next slot;
    the daemon itself must never die because a job raised (watchdog doctrine).
  • Logs append to logs/scheduler.log AND stdout.

Start it:            .venv/bin/python scheduler.py
Keep it alive:       nohup .venv/bin/python scheduler.py >> logs/scheduler.log 2>&1 &
Test a job now:      python scheduler.py --once mark
                     python scheduler.py --once daily
Show the schedule:   python scheduler.py --list

PAPER ONLY — this schedules simulations; nothing places a real order.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
IST = ZoneInfo("Asia/Kolkata")

DAILY_HOUR, DAILY_MIN = 15, 45          # after the 15:30 close
MARK_HOURS = "10-15"                    # 10:15, 11:15, … 15:15 (15:15 = last full hour)
MARK_MINUTE = 15
MISFIRE_GRACE_S = 30 * 60               # run a missed slot if we wake within 30 min

log = logging.getLogger("scheduler")


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "scheduler.log"),
        ],
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


# ── Jobs (each wrapped fail-soft: log the error, keep the daemon alive) ───────

def daily_pipeline() -> None:
    """The existing nightly chain, unchanged — run as a subprocess so one bad
    stage can never take the scheduler down with it."""
    log.info("daily_pipeline: starting run_paper_bot.sh")
    try:
        res = subprocess.run(
            ["/bin/bash", str(BASE_DIR / "run_paper_bot.sh")],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=3600,
        )
        for line in (res.stdout or "").splitlines():
            log.info("  %s", line)
        if res.returncode != 0:
            log.error("daily_pipeline: exited %s\n%s", res.returncode, res.stderr)
        else:
            log.info("daily_pipeline: complete")
    except Exception:
        log.exception("daily_pipeline: crashed (daemon continues)")


def hourly_mark() -> None:
    """Mark + risk pass; intraday_mark has its own session guard."""
    try:
        import intraday_mark
        rc = intraday_mark.main()
        if rc != 0:
            log.error("hourly_mark: returned %s", rc)
    except Exception:
        log.exception("hourly_mark: crashed (daemon continues)")


# ── Wiring ────────────────────────────────────────────────────────────────────

def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone=IST)
    common = dict(coalesce=True, max_instances=1,
                  misfire_grace_time=MISFIRE_GRACE_S)
    sched.add_job(
        daily_pipeline, id="daily_pipeline", name="daily paper-bot pipeline",
        trigger=CronTrigger(day_of_week="mon-fri", hour=DAILY_HOUR,
                            minute=DAILY_MIN, timezone=IST),
        **common,
    )
    sched.add_job(
        hourly_mark, id="hourly_mark", name="hourly mark + risk governor",
        trigger=CronTrigger(day_of_week="mon-fri", hour=MARK_HOURS,
                            minute=MARK_MINUTE, timezone=IST),
        **common,
    )
    return sched


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper-bot scheduling daemon")
    ap.add_argument("--once", choices=["daily", "mark"],
                    help="run one job immediately and exit (manual test)")
    ap.add_argument("--list", action="store_true",
                    help="print the schedule and exit")
    args = ap.parse_args()
    _setup_logging()

    if args.once == "daily":
        daily_pipeline()
        return 0
    if args.once == "mark":
        hourly_mark()
        return 0

    sched = build_scheduler()
    if args.list:
        for job in sched.get_jobs():
            log.info("%-16s %-32s next: %s", job.id, str(job.trigger),
                     getattr(job, "next_run_time", None) or "(scheduler not started)")
        return 0

    log.info("scheduler up — daily %02d:%02d IST, hourly marks %s:%02d IST "
             "(Mon–Fri, NSE-session guarded). Ctrl-C to stop.",
             DAILY_HOUR, DAILY_MIN, MARK_HOURS, MARK_MINUTE)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
