#!/bin/bash
# run_paper_bot.sh — one daily unattended PAPER run of the whole bot.
# Fetches fresh prices, runs the options sims (NIFTY short strangle + defined-risk
# iron condor, head-to-head), runs the research automation pipeline (validate →
# features → factors → backtests → walk-forward → reports → dashboard), then the
# digest.
# SIMULATED / RESEARCH ONLY — every "trade" is a local DB row; nothing places a
# real order. fetch_data.py downloads daily OHLCV via yfinance; research_pipeline.py
# only reads cached data and writes results/*.json (incl. pipeline_run.json, the
# run record the Automation page shows). A failed fetch (e.g. no network) is
# non-fatal — later steps just use the existing cached data.
#
# Intraday (ORB + VWAP) was RETIRED 2026-06-26: the monitoring sandbox delivered
# its finding — a thin intraday edge that does not survive realistic MIS costs
# (combined gross +Rs11,405 vs costs -Rs32,915 over ~10 days). See
# strategies/SPEC_intraday.md / SPEC_vwap.md CONCLUDED notes. intraday.db is kept
# as evidence; intraday_sim.py is left in the repo but no longer run daily.
cd /Users/samvid/projects/tradebot || exit 1
PY=".venv/bin/python"

# A book runs only if it's enabled in the dashboard (Strategies → toggle, stored
# in controls.db). Unknown/unset keys default to enabled, so behaviour is
# unchanged unless you switch a book off. A flag-check error never blocks a run.
is_on(){ "$PY" controls.py is-enabled "$1" 2>/dev/null; rc=$?; [ "$rc" -ne 1 ]; }

echo "================ paper-bot run $(date '+%Y-%m-%d %H:%M:%S %Z') ================"

# ── NSE calendar guard (CODE_AUDIT_2026-07-10 §A4) ────────────────────────────
# Skip the whole run on a non-session day (weekend / known holiday) so nothing
# marks or rebalances at stale prices. FAIL-OPEN: rc 1 = "not a session" skips;
# any other outcome (calendar error, missing venv) lets the run proceed —
# a broken helper must never block the bot, matching the is_on() doctrine.
# (paper_trader.py additionally defers its rebalance if today's bar is missing,
# which catches variable-date holidays this forward calendar can't know.)
"$PY" -c "
import sys, datetime, trading_calendar
sys.exit(0 if trading_calendar.TradingCalendar().is_session(datetime.date.today()) else 1)
" 2>/dev/null
if [ $? -eq 1 ]; then
  echo "NSE non-session day (weekend/holiday) — skipping today's run."
  exit 0
fi

echo "----- fetch fresh prices (yfinance; non-fatal on failure) -----"
"$PY" fetch_data.py --refresh || echo "  (fetch failed — continuing on cached data)"
if is_on strangle; then
  echo "----- options (NIFTY short strangle) -----"; "$PY" options_sim.py
else
  echo "----- options (NIFTY short strangle) — DISABLED in dashboard, skipped -----"
fi
if is_on condor; then
  echo "----- options (NIFTY iron condor, defined-risk) -----"; "$PY" condor_sim.py
else
  echo "----- options (NIFTY iron condor, defined-risk) — DISABLED in dashboard, skipped -----"
fi
echo "----- research automation pipeline (validate → features → factors → backtests → walk-forward · Monte Carlo → reports → summary → archive) -----"
"$PY" research_pipeline.py --no-fetch   # data already fetched above; runs the full 10-stage chain + writes results/pipeline_run.json, research_summary.md, results/archive/<date>/
echo "----- promotion advisor (pre-registered lifecycle rules; advise-only unless promotion_rules.json auto_execute=true) -----"
"$PY" promotion_advisor.py || echo "  (advisor failed — non-fatal)"
echo "----- daily digest -----"
"$PY" digest.py
echo "----- n8n run report (email digest via n8n; fail-soft, needs N8N_RUN_WEBHOOK in .env) -----"
"$PY" notify_n8n.py || echo "  (n8n notify failed — non-fatal)"
echo "================ paper-bot run complete ================"
echo
