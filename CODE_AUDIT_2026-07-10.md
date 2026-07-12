# Code & Architecture Audit — 2026-07-10

Scope: full repo scan — test suite (265 tests), static analysis (ruff), compile check,
manual review of execution engine, controls, web/auth, webhook, data layer, deploy.
Every finding below was verified against the actual code, not assumed.

Verdict: **solid engineering for a research/paper system** — allow-listed subprocess
launcher, read-only DB handles, constant-time secret compares, secrets never committed,
centralized cost model, spec-driven strategy lifecycle. The findings are fixable drift
and a few real bugs, not systemic rot.

---

## A. Broken functions / bugs (verified)

### A1. `backtest.py` CLI crashes — NameError (HIGH)
`backtest.py:364` uses `SLIPPAGE_PER_SIDE` in main's print, but line 19 imports only
`COST_ENTRY, COST_EXIT, COST_ROUNDTRIP` from config. `python backtest.py` dies with
`NameError` before printing results. (Daily pipeline is unaffected — it calls
`backtest_lowvol.main()` / `backtest_momentum.main()`, not this file.)
**Fix:** add `SLIPPAGE_PER_SIDE` to the config import on line 19.

### A2. Test suite is RED — 1 failure from retirement drift (HIGH)
`test_controls.py::test_settle_now_closes_and_realises` fails: it expects
`options_sim.open_cycle()` to open a strangle cycle, but `options_sim.RETIRED = True`
(2026-07-08 human decision) makes it a no-op returning None. A permanently red suite
destroys the signal value of all 265 tests — the next real regression hides behind it.
**Fix:** in the test, `monkeypatch.setattr(options_sim, "RETIRED", False)` (it already
uses monkeypatch), or port the test to `condor_sim`.

### A3. Test data polluted the production run record (HIGH)
`results/pipeline_run.json` — the file the Automation page and the new watchdog read —
currently contains a demo record (stages "good"/"bad"/ZeroDivisionError, duration 0s,
generated 2026-07-09). A test or demo run wrote into the live `results/` path and
clobbered the real run record.
**Fix:** research_pipeline should honour an env override for its output dir (like
`CONTROLS_DB`/`TV_DB` already do), and tests must set it to `tmp_path`. Audit other
tests for writes to real paths.

### A4. No trading-calendar guard on the daily run (HIGH)
`trading_calendar.py` exists but `paper_trader.main()` and `run_paper_bot.sh` never call
`is_session()`. launchd fires Mon–Fri including NSE holidays; if the 1st trading day of
a month is a holiday, the monthly rebalance executes at the prior session's stale closes
— fills at prices nobody could trade. Same for options sims' marks.
**Fix:** first line of each daily entrypoint:
`if not Calendar().is_session(date.today()): sys.exit(0)` — the month-guard then
naturally rebalances on the next real session.

### A5. Partial rebalance marks the month as done (MEDIUM)
`rebalance()` sets `last_rebalance_month` unconditionally at the end. If several names
were skipped ("no price", "short on cash"), the book stays mis-weighted for a month with
no retry.
**Fix:** persist the target portfolio; on subsequent daily runs reconcile
holdings-vs-target and top up when a skipped name becomes priceable.

## B. Architectural issues

### B1. TV webhook design exposes the whole app to the internet (HIGH)
`tv_signals.py` header instructs tunnelling (`cloudflared`/`ngrok`) to reach
`/api/tv/webhook`. A tunnel to the Flask app exposes **everything**: the login page and
`/command/control/*` — routes that spawn subprocesses — behind one static password with
**no rate limiting or lockout** (verified: none in `dashboard.py`). The payload-secret
compare is constant-time (good), but brute force is unbounded.
**Fix (any of, in order of preference):**
1. Cloudflare Tunnel path rule exposing ONLY `/api/tv/webhook`;
2. run the webhook as a separate minimal Flask app on its own port and tunnel that;
3. at minimum add login rate-limiting (e.g. 5 attempts → 15-min lockout) and enforce a
   long random `DASHBOARD_SECRET_KEY` (session cookies are forgeable if it's weak).

### B2. No daily NAV persistence for the flagship low-vol book (MEDIUM)
`tv.db` has a `marks` table; `portfolio.db` does not. Hold-day marks are printed to a
log and discarded — the book's equity curve/drawdown history can't be reconstructed
exactly (dashboard re-derives it ad hoc). For a track record you intend to show anyone
(FUND_BLUEPRINT), the NAV series is the product.
**Fix:** add the same `marks` table to portfolio.db; write one row per run (both
rebalance and hold days).

### B3. No backup of the ledgers (MEDIUM — data-loss risk)
Every DB (`portfolio.db`, `condor.db`, `tv.db`, `controls.db`, …) and `results/` are
gitignored — correct for git — but there is **no backup mechanism at all**. One disk
failure erases the entire paper track record, which is the evidence your fund blueprint
depends on.
**Fix:** nightly `sqlite3 <db> ".backup"` of all ledgers to a dated folder synced
off-machine (iCloud/rclone). One 5-line script + one launchd line in the existing plist.

### B4. Layering inversion: web layer imports the trading engine (LOW)
`web_common.py` (web) imports `fetch_live` from `paper_trader.py` (engine). Harmless
today, but it couples dashboard startup to engine internals.
**Fix:** move `fetch_live`/`build_panel` into `data_io.py`/`data_layer.py`; both engine
and web import from the data layer.

### B5. Controls allow-list has drifted from strategy reality (LOW)
`controls.py STRATEGIES`: `intraday` (retired 06-26) still runnable & default-enabled;
`strangle` (retired 07-08) default True — `run_paper_bot.sh` still invokes it daily (a
harmless but noisy no-op); `momentum`'s "run" launches a *backtest*, not a sim.
**Fix:** align the table with PLAYBOOK status; retired books → `script: None`,
`default: False`.

### B6. tv_signals paper book allows implicit leverage (LOW)
`apply()` caps per-name weight (15%) but never checks cash on BUY — enough distinct
symbols → negative cash → an unrealistically leveraged paper book, corrupting the very
evidence the sandbox exists to produce. Also: webhook has no rate limit; `signals`
table grows unboundedly on spam.
**Fix:** reject BUY when `cash - trade_cost < 0`; add a per-minute accept cap.

## C. Hygiene / minor

- **C1.** ~21 files uncommitted (9 modified incl. `paper_trader.py`, `dashboard.py`;
  whole futures book + PLAYBOOK untracked). Commit in logical units — you can't bisect
  or roll back what isn't committed.
- **C2.** Ruff: 32 unused imports, 30 placeholder-less f-strings, 2 unused variables
  (`cost_gate.py:61 gross_R` — looks like an intended gross-vs-net comparison that was
  never finished — and `risk_report.py:65 closes`). `ruff check --fix` clears 61.
- **C3.** `dashboard.py:612` `use_reloader=True` — fine for dev, but under a daemon it
  forks a second watcher process. Gate it: `use_reloader=os.getenv("DEV") == "1"`.
- **C4.** `requirements.txt` pins (pandas 3.0.3, numpy 2.4.6) require Python ≥3.12 but
  no interpreter version is documented/pinned. Add `requires-python`/note, or a
  `.python-version` file.
- **C5.** `run_paper_bot.sh is_on()` fails OPEN on any rc≠1 (including 127 =
  interpreter missing). Deliberate, but consider distinguishing "flag off" from
  "controls.py broken" with a log line.

## D. What's already good (keep)

Read-only SQLite URIs for all dashboard reads; hard allow-list + path containment for
UI-launched jobs; `hmac.compare_digest` on both secrets; secrets/ledgers gitignored and
never in git history (verified); single-source cost model in config; spec-first strategy
lifecycle with human-gated retirement flags; 264/265 tests passing with real security
boundary tests.

## Priority order

1. A1 (one-line fix), A2 (test red → green)  — restores trust in the suite. ✅ both fixed in this pass
2. A4 calendar guard, A3 results isolation    — protects data integrity of every future run.
3. B1 webhook exposure                        — do before ever starting the tunnel.
4. B3 backups, B2 NAV marks                   — protects the track record.
5. The rest as convenient.
