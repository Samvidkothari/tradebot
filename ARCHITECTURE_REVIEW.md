# Architecture Review — Tradebot

*Lead Architect review, 2026-06-27. Grounded in the measured import graph + code scan.*

> All items #1–8 of §10 were implemented immediately after this review (see commit
> history). All paper/research — no order-placement code.

## 1. Current architecture

Layered research/paper-trading system, bottom-up:

```
 L0  Shared base      config.py · data_io.py
 L1  Signals          lowvol · momentum · strategy(SMA)        (target_portfolio rules)
 L2  Backtest/engine   backtest · backtest_lowvol/momentum · strategy_base
 L3  Paper simulators  paper_trader · options_sim · condor_sim · intraday_sim → *.db
 L4  Research engine   metrics · regime · factors · portfolio_analyzer ·
                       risk_analytics · attribution · data_quality (+ *_report → results/*.json)
 L5  Presentation      dashboard.py + features.py (Flask) · digest.py
 ──  Side branch       Kite read-only: login · exchange · kite_client · watch
```

Flow is unidirectional: signals → engine → sims/research → JSON → dashboard.

## 2. Dependency graph (measured)

Fan-in: `config` ←9 · `strategy_base` ←5 · `data_io` ←5 · `lowvol`/`backtest`/`backtest_lowvol` ←4.
Graph is acyclic and layered, but two edges point the wrong way (§4).

## 3. Duplicate code
- Formatter helpers — 8 copies (`rupees`×4 in sims; `_pct`/`_f`/`_r`×4 in research runners).
- `RESULTS_DIR.mkdir(exist_ok=True)` in 9 files.
- (Resolved earlier: rebalance engine, dashboard JSON routes, `load_panel`, `CAPITAL`.)

## 4. Tightly coupled modules
1. **Research → pre-registered backtest:** `tearsheet`/`risk_report`/`attribution_report` import `backtest_lowvol` only for `SPLIT_DATE` + the re-exported `load_panel`. Both belong lower (config / data_io).
2. **Cost model in the retired SMA backtest:** `COST_ENTRY/EXIT/ROUNDTRIP` defined in `backtest.py` but imported by lowvol/momentum/paper_trader/strategy_base.
3. **`dashboard.py` hub** (802 lines) importing paper_trader/digest/features/kite_client/config.

## 5. Technical debt
- Cost model + `SPLIT_DATE` sourced from strategy/backtest modules, not config.
- Implicit, unvalidated `results/*.json` schemas (runner→template).
- `dashboard.py` (802) / `features.py` (652) oversized; `features.py` holds the `# LIVE EXECUTION HOOK` marker.
- No integration/smoke test for `run_paper_bot.sh` or dashboard routes.
- Generated JSONs uncommitted → research tabs blank on fresh checkout.

## 6. Dead code
- `DATA_DIR` defined-but-unused in `backtest_lowvol`, `backtest_momentum`, `portfolio_analyzer` (data_io refactor residue).
- `strategy.py` + SMA logic in `backtest.py` (retired; backtest.py still mined for cost/benchmark utils).
- `intraday_sim.py` retired (documented as evidence, not dead).
- `review.py` not imported anywhere — verify or archive.

## 7. Scalability
- `load_panel()` reloads all 48 CSVs in each runner → ~6× per `refresh_research`. Fine now, bites at 500+ names.
- SQLite single-file books — correct for single local user.
- Good seams: new strategy = 1 REGISTRY line; new research page = 2-line route; new data source = data_io.

## 8. Performance
- Redundant panel loads (§7) — the one real inefficiency.
- Live yfinance fetches on `/pnl`, `/paper`, `/` (memoised 5 min) — acceptable.
- Monte Carlo / backtests sub-second.

## 9. Code smells
- Core constants (cost model) in a retired strategy file.
- `CAPITAL` vs `STARTING_CAPITAL` (two names, now both alias config).
- Stringly-typed dict payloads across runner→JSON→template.
- Two god modules (dashboard, features).
- `backtest.py` doubles as the SMA backtest AND the shared cost/benchmark provider.

## 10. Improvements ranked by ROI
| # | Improvement | Effort | Risk | ROI |
|---|---|---|---|---|
| 1 | Delete the 3 dead `DATA_DIR` vars | trivial | ~0 | High |
| 2 | Move cost model + `SPLIT_DATE` into `config`; repoint imports | low | low | High (kills §4.1/§4.2) |
| 3 | Load the panel **once** per `refresh_research` (memoise) | low | low | High |
| 4 | Integration smoke test (pipeline JSON + all routes) | low-med | low | High ✅ done (`test_integration.py`) |
| 5 | Dataclasses/TypedDicts for `results/*.json` payloads | medium | low | Medium ✅ done (`schemas.py` — TypedDicts + enforced `validate`) |
| 6 | Split `dashboard.py` into Flask blueprints | medium | medium | Medium ✅ done (research views → views_research.py + shared web_common.py; 803→530 lines; endpoints preserved) |
| 7 | Archive `review.py` / `intraday_sim.py` explicitly | trivial | ~0 | Medium ✅ done (status banners) |
| 8 | Consolidate the 8 formatter helpers (research side) | low | low | Low ✅ done (`fmt.py`; sim `rupees()` left intentionally) |

**Guardrails:** pre-registered backtest verdicts stay byte-identical (constants-only moves, proven by regression test + re-run); no order path introduced.

## Verdict
Healthy, layered, genuinely extensible — with one structural wart (shared concerns
leaking through the retired/strategy-specific `backtest.py`/`backtest_lowvol.py`)
and minor refactor residue. #1–4 are high-ROI / low-risk.
