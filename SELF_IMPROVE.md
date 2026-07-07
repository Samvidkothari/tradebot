# Self-Improvement Charter

*Pre-committed 2026-07-06. These rules are the contract for the autonomous loops.
They may be changed by a human, in a reviewed commit — never by an automated run.*

The system may **propose, test, and de-risk** autonomously. Only a human may
**promote, fund, or merge**. All activity is paper/simulated; no order-placement
code exists or may be added by any automated run.

---

## Loop 1 — Decay detection & de-risking (daily)

Checked as part of the daily digest run, using existing artifacts
(`results/` tear sheets, walk-forward, `research_assistant` findings).

Pre-committed rules:
- **Decay floor:** a live sleeve whose rolling out-of-sample Sharpe (walk-forward,
  latest window) is below **0.30** for **10 consecutive trading days** is flagged
  `DECAY-WATCH`.
- **Action on flag:** recommendation to halve the sleeve's risk budget appears in
  the daily digest + a re-audition ticket is filed in `results/thesis_register.json`
  (`kind: "re-audition"`). No automated change to any pre-registered spec.
- **Recovery:** flag clears only after 20 consecutive days back above the floor.
- Parameters may adapt over time **only if** the sleeve's SPEC declared them
  adaptive before testing. Anything re-tuned in response to bad results is
  overfitting and is forbidden.

## Loop 2 — Research generation (weekly)

- Scouts (automated runs) may write new `strategies/THESIS_*.md` +
  `strategies/SPEC_*.md` drafts and run the validation gauntlet
  (OOS split · walk-forward · Monte Carlo · **cost stress at 1.5×** the
  config cost model).
- Every idea tested is logged in `results/thesis_register.json` before testing.
  **Multiple-testing haircut:** required OOS Sharpe to pass = 0.8 + 0.05 × (ideas
  already tested this quarter).
- Default verdict is **reject**. A passing idea is marked `CANDIDATE` and waits
  for human promotion. Automated runs never add a strategy to the live REGISTRY,
  never modify existing pre-registered specs, never allocate budget.
- Unbounded-tail structures (e.g. naked short options) are auto-rejected
  regardless of backtest results.

## Loop 3 — Code self-update (weekly)

- Scope: items from `TECH_DEBT.md`, failing/flaky tests, review-doc open items,
  doc drift. **One item per run.**
- Method: isolated git worktree → patch → full test suite (`run_tests.py` or
  `pytest`) + `smoke_test.py` must pass → diff + rationale left for human review
  as `results/self_update/<date>_<slug>.md` (and the worktree branch kept).
- Hard exclusions (never modified by an automated run): pre-registered strategy
  files (`backtest_lowvol.py`, `backtest_momentum.py`, `lowvol.py`, `momentum.py`,
  `regime_overlay.py`, everything in `strategies/` that is already committed),
  ledger DBs, `config.py` cost model, this charter.
- **Never merge.** A failing test kills the patch; the run reports the failure
  instead.

## Kill switch

If the file `HALT_SELF_IMPROVE` exists in the repo root, every loop must do
nothing except report that it is halted. Only a human creates or removes it.
