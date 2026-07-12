#!/bin/bash
# watchdog.sh — safety net for the daily unattended paper-bot run.
#
# Runs at 16:30 IST Mon–Fri (via deploy/com.tradebot.watchdog.plist), i.e.
# 45 min after the 15:45 scheduled run. It checks whether today's run actually
# happened by reading results/pipeline_run.json ("generated" date, written at
# the end of research_pipeline.py). If the run is missing or any stage failed:
#
#   1. Retry: kick off run_paper_bot.sh once (only if the run is MISSING,
#      not if it ran with a failed stage — no point re-running a code bug).
#   2. Notify: post a macOS notification either way, so a silent miss is
#      never silent.
#
# Exit codes: 0 = run ok / retry succeeded, 1 = run missing and retry failed,
#             2 = run happened but a stage failed.
#
# Idempotent and safe: run_paper_bot.sh is a PAPER pipeline (no real orders),
# and paper_trader/options sims are documented as harmless to run twice a day.

cd /Users/samvid/projects/tradebot || exit 1
LOG="logs/watchdog.log"
RUNFILE="results/pipeline_run.json"
TODAY=$(date '+%Y-%m-%d')

notify() {  # notify "message"  — macOS banner + log line
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"
  /usr/bin/osascript -e "display notification \"$1\" with title \"tradebot watchdog\"" 2>/dev/null
}

ran_today() {
  [ -f "$RUNFILE" ] && grep -q "\"generated\": \"$TODAY\"" "$RUNFILE"
}

stage_failed() {
  [ -f "$RUNFILE" ] && grep -q '"status": "failed"' "$RUNFILE"
}

# Weekend guard (launchd plist is Mon–Fri anyway; belt-and-braces).
dow=$(date '+%u'); [ "$dow" -gt 5 ] && exit 0

# NSE holiday guard — run_paper_bot.sh skips non-session days by design, so a
# missing run record on a holiday is CORRECT, not a failure. Same fail-open
# contract: only an explicit "not a session" (rc 1) exits; errors proceed.
.venv/bin/python -c "
import sys, datetime, trading_calendar
sys.exit(0 if trading_calendar.TradingCalendar().is_session(datetime.date.today()) else 1)
" 2>/dev/null
if [ $? -eq 1 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') NSE non-session day — nothing to watch" >> "$LOG"
  exit 0
fi

if ran_today; then
  if stage_failed; then
    notify "Run happened today but a pipeline stage FAILED — check dashboard/Automation."
    exit 2
  fi
  echo "$(date '+%Y-%m-%d %H:%M:%S') run ok — nothing to do" >> "$LOG"
  exit 0
fi

# Missed run → retry once.
notify "No paper-bot run found for $TODAY — retrying now."
/bin/bash run_paper_bot.sh >> logs/paperbot.log 2>&1

if ran_today && ! stage_failed; then
  notify "Retry succeeded — today's run is complete."
  exit 0
else
  notify "Retry FAILED — paper-bot did not complete. Check logs/paperbot.log."
  exit 1
fi
