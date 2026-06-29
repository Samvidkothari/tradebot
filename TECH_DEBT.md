# Tech-Debt Register

A deliberate record of known technical debt and the **decisions** about it, so each
pass of the continuous-development loop doesn't re-litigate the same items. Updated
2026-06-29.

Principle applied (from the engineering brief): *optimise for robustness and
maintainability; never optimise for complexity.* A refactor only earns its place if
the benefit clearly outweighs the churn/regression risk — especially for
**pre-registered** strategy code, whose committed verdicts must stay byte-identical.

## Resolved this session
- ✅ **Duplicated monthly-rebalance engine** → `strategy_base.MonthlyRebalanceEngine`
  (regression-proven identical). Commit `8499fe9`.
- ✅ **Six copy-pasted dashboard JSON routes** → `_research_json()` helper. Commit `c1f432d`.
- ✅ **Byte-identical `load_panel` + scattered CSV loaders** → `data_io.py`
  (verdicts proven unchanged). Commit `075494b`.
- ✅ **`CAPITAL = 1_000_000` duplicated in 9 files** → `config.PAPER_CAPITAL`
  (single source of truth). All 9 now alias the config value; proven identical
  (each resolves to 1_000_000 / `is config.PAPER_CAPITAL`), 40 tests pass,
  research + dashboard unaffected. Done at the user's explicit request.

## Resolved 2026-06-29
- ✅ **`features.py` (652 lines) was the next thing to outgrow itself** → split into a
  `features/` package (`core` / `analytics` / `journal` / `alerts` / `ticket` /
  `exports`) using the blueprint pattern. Public surface byte-identical
  (`from features import bp` unchanged), so `dashboard.py` didn't move. This is the
  same prescription item #3 records for `dashboard.py` — applied first to the module
  that actually grew. Commits `521b043`, `c9de162`.
- ✅ **No read-only enforcement on ledger queries** → `web_common.ro_db()` opens a
  `mode=ro` SQLite URI for every read path; `rw_db()` is the only writer and is
  confined to the isolated feature DBs. A path-traversal/input-validation guard and
  an explicit "ticket only ever records SIMULATED" assertion are now pinned by
  `test_security_boundaries.py`. Commit `89b0907`.
- ✅ **No web-layer regression net** (the part that historically shipped 500s) →
  `smoke_test.py` (boots the app in-process, GETs all 40 routes, asserts 200) plus
  `test_routes` / `test_features_metrics` / `test_intraday_sim`. All read-only.
  Commit `58d3084`.

## Open items — with decisions

### 2. Small formatter helpers (`rupees`/`_pct`/`_f`) duplicated ~8× — WON'T FIX
4 copies live in pre-registered/live sims (don't touch), 4 in research runners.
Consolidating only the research ones saves ~8 lines while adding an import
dependency — marginal. **Decision: leave it** unless a shared `format.py` is
introduced for another reason.

### 3. `dashboard.py` is ~800 lines — MONITOR
Large but coherent (one Flask app, many small routes). Not a problem yet. **If** it
grows further, split into blueprints (live / paper / options / research), mirroring
the `features/` package split done 2026-06-29 — that split is now the working
precedent for how to do this cleanly (keep the public import surface stable so the
app wiring doesn't move). Still not worth doing pre-emptively.

## Standing constraints that intentionally create "duplication"
- **Pre-registered backtests (`backtest_lowvol.py`, `backtest_momentum.py`)** keep
  their own `evaluate`/report code rather than sharing a base, to preserve
  pre-registration integrity. This is deliberate, not debt.
- **No order-placement / execution layer exists by design** — the "Trading Engine"
  from the platform briefs is intentionally unbuilt and gated behind a separate,
  explicit go-live decision. Absence is a feature, not debt.

## Known data/scope limits (not code debt)
- Price/volume data only (no fundamentals) → fundamental factors unbuilt on purpose.
- Survivorship bias in the universe (current NIFTY 50 membership) — acknowledged.
- `LTIM`, `TATAMOTORS` fail to download (delisted yfinance tickers).
