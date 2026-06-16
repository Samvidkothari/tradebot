#!/bin/bash
# run_paper_bot.sh — one daily unattended PAPER run of the whole bot.
# Runs the intraday sims (ORB + VWAP) and the options sims (NIFTY short strangle
# + defined-risk iron condor, head-to-head).
# SIMULATED ONLY — every "trade" is a local DB row; nothing places a real order.
cd /Users/samvid/projects/tradebot || exit 1
PY=".venv/bin/python"

echo "================ paper-bot run $(date '+%Y-%m-%d %H:%M:%S %Z') ================"
echo "----- intraday (ORB + VWAP) -----"
"$PY" intraday_sim.py
echo "----- options (NIFTY short strangle) -----"
"$PY" options_sim.py
echo "----- options (NIFTY iron condor, defined-risk) -----"
"$PY" condor_sim.py
echo "----- daily digest -----"
"$PY" digest.py
echo "================ paper-bot run complete ================"
echo
