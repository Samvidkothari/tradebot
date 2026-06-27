# Tech-Debt Register

A deliberate record of known technical debt and the **decisions** about it, so each
pass of the continuous-development loop doesn't re-litigate the same items. Updated
2026-06-27.

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

## Open items — with decisions

### 1. `CAPITAL = 1_000_000` duplicated in ~9 files — WON'T FIX (for now)
`dashboard.py` even comments "must match paper_trader.py / intraday_sim.py".
**Decision: leave it.** It is a constant that is identical everywhere and never
changes; unifying it into a `config.py` would touch the **pre-registered sims**
(`options_sim`, `condor_sim`, `intraday_sim`) and the **live `paper_trader`** for
zero behavioural benefit. The regression/review risk to protected code outweighs
the single-source-of-truth gain. **Revisit only if** the paper capital ever needs
to differ per book or change — at which point a `config.py` becomes worth it.

### 2. Small formatter helpers (`rupees`/`_pct`/`_f`) duplicated ~8× — WON'T FIX
4 copies live in pre-registered/live sims (don't touch), 4 in research runners.
Consolidating only the research ones saves ~8 lines while adding an import
dependency — marginal. **Decision: leave it** unless a shared `format.py` is
introduced for another reason.

### 3. `dashboard.py` is ~800 lines — MONITOR
Large but coherent (one Flask app, many small routes). Not a problem yet. **If** it
grows further, split into blueprints (live / paper / options / research), mirroring
the existing `features.py` blueprint pattern. Not worth doing pre-emptively.

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
