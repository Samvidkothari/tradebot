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

echo "================ paper-bot run $(date '+%Y-%m-%d %H:%M:%S %Z') ================"
echo "----- fetch fresh prices (yfinance; non-fatal on failure) -----"
"$PY" fetch_data.py --refresh || echo "  (fetch failed — continuing on cached data)"
echo "----- options (NIFTY short strangle) -----"
"$PY" options_sim.py
echo "----- options (NIFTY iron condor, defined-risk) -----"
"$PY" condor_sim.py
echo "----- research automation pipeline (validate → features → factors → backtests → walk-forward · Monte Carlo → reports → summary → archive) -----"
"$PY" research_pipeline.py --no-fetch   # data already fetched above; runs the full 10-stage chain + writes results/pipeline_run.json, research_summary.md, results/archive/<date>/
echo "----- daily digest -----"
"$PY" digest.py
echo "================ paper-bot run complete ================"
echo
