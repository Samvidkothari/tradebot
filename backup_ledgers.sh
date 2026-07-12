#!/bin/bash
# backup_ledgers.sh — nightly backup of every paper ledger (CODE_AUDIT §B3).
#
# The SQLite ledgers + pipeline artefacts are gitignored (correctly), which means
# git protects NONE of the track record. This script snapshots them with
# `sqlite3 .backup` (safe against a concurrently-open DB, unlike cp) into
# backups/YYYY-MM-DD/ and keeps 30 days. Point backups/ at a synced folder
# (iCloud/Dropbox symlink) to get it off-machine.
#
# Scheduled at 17:00 daily via deploy/com.tradebot.backup.plist. Read-only
# against the ledgers; never modifies application state.

cd /Users/samvid/projects/tradebot || exit 1
DEST="backups/$(date '+%Y-%m-%d')"
LOG="logs/backup.log"
mkdir -p "$DEST" logs

say(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }

fail=0
count=0
for db in *.db; do
  [ -e "$db" ] || continue
  if sqlite3 "$db" ".backup '$DEST/$db'" 2>>"$LOG"; then
    count=$((count+1))
  else
    say "ERROR backing up $db"
    fail=1
  fi
done

# Pipeline artefacts worth keeping alongside the ledgers (small, high value).
for f in results/pipeline_run.json results/research_summary.md; do
  [ -e "$f" ] && cp "$f" "$DEST/" 2>>"$LOG"
done

# Retention: keep 30 daily snapshots.
find backups -mindepth 1 -maxdepth 1 -type d -name '20*' | sort | head -n -30 | \
  while read -r old; do rm -rf "$old"; say "pruned $old"; done

if [ "$fail" -eq 1 ]; then
  say "backup FINISHED WITH ERRORS ($count DBs ok) → $DEST"
  /usr/bin/osascript -e 'display notification "Ledger backup had errors — check logs/backup.log" with title "tradebot backup"' 2>/dev/null
  exit 1
fi
say "backup ok: $count DBs → $DEST"
