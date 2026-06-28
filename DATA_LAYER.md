# Data Layer

A tested orchestration layer over the existing data modules, giving Research one
clean entry point. **Additive — it does not replace the pre-registered backtests'
`data_io.load_panel` path (those stay byte-identical). READ-ONLY; no orders.**

```
MarketDataManager → DataValidator → CorporateActionManager → FeatureStore → Research
        │                 │                  │                    │
   fetch + load       data_quality      detect/log/flag      version-keyed cache
   + calendar +       (OK/WARN/FAIL)    (auto-adjusted)       (fast reloads)
   version
```

## Requirements → how they're met
| Requirement | How |
|---|---|
| **No duplicated downloads** | `IncrementalUpdater` fetches only sessions *after* each symbol's last cached date (`plan()` / `plan_all()`); a corporate-action rebase triggers a one-off full re-fetch. |
| **Cached calculations** | `FeatureStore` caches factor scores in-memory **and** on disk, keyed by the data **version** (auto-invalidates when data changes). |
| **Fast reloads** | Disk feature cache (`data/_feature_cache/`) + memoised `data_io` loaders (one CSV read per process). |
| **Clean interfaces** | Each stage is a small class with a focused API; `DataPipeline` chains them. |

## Components (`data_layer.py`, plus `trading_calendar.py`, `data_version.py`)

### MarketDataManager
Single source for data. `close_panel()`, `volume_panel()`, `ohlcv(sym)`, `nifty()`,
`universe()`, `context()`, `as_of()`; `refresh()` (full) / `update()` (incremental);
`.calendar` (TradingCalendar), `.version` / `.manifest` (content hash of the snapshot).

### DataValidator
Wraps `data_quality`. `report()`, `summary()`, `is_clean()`, `usable_symbols()`,
`failing_symbols()`, `assert_usable()` (raises `DataQualityError` on FAIL).

### CorporateActionManager
**Honest scope:** yfinance `auto_adjust=True` means prices are *already*
split/dividend-adjusted. So this stage records that (`is_adjusted`), `detect()`s /
`flags()` residual extreme jumps that may be *unhandled* actions, `log()`s them, and
reports `symbols_needing_refetch()` to the updater. It does **not** fabricate split
ratios — we have no raw feed / actions calendar. `adjust()` is the hook for real
back-adjustment if such a feed is added. (`CorporateActionAdjuster` is a kept alias.)

### FeatureStore + FeatureCache
`get(factor, as_of)`, `scores(as_of)`, `composite(weights, as_of)`. Lookups go
in-memory → disk (`FeatureCache`, version-keyed) → compute. `FeatureCache` files
are named `<version>_<factor>_<pos>.pkl`; a new version writes new files, so stale
features never leak; `prune_other_versions()` cleans old ones.

### IncrementalUpdater
`plan(last_date, today)` (pure → `full`/`incremental`/`uptodate`), `plan_all()`
(pure, all symbols, no network), `update()` (network: append-only, with a
rebase-detection full re-fetch on corporate actions).

### TradingCalendar (`trading_calendar.py`)
NSE sessions = the dates the **NIFTY index actually traded** (ground truth, holidays
included automatically — no list to rot). `is_session`, `session_range`, `n_sessions`,
`next/prev/last_session`, `missing_sessions(symbol)`. Forward dates beyond the data
fall back to "weekday & not a fixed national holiday" — an accurate forward calendar
needs an official NSE feed we don't have (stated, not faked).

### DataManifest / versioning (`data_version.py`)
`build()` → `{symbol: {last_date, n_rows, sha1}}`; `version` = 12-char hash that
changes iff content changes; `changed_symbols(previous)`; `write()`/`read()`
(`data/_manifest.json`). Used to key the feature cache and to drive incremental updates.

## Universe Manager (`universe.py` + `universes.json`)
Configuration-driven stock universes — **no hardcoded lists in code** (membership
lives in `universes.json`). Supports `NIFTY50`, `NIFTY_NEXT_50`, `NIFTY100`,
`NIFTY200` (composable via unions), `fno`, sector-wise, and custom universes.

```python
from universe import UniverseManager
um = UniverseManager()
um.members("NIFTY200")     # full configured membership (composed)
um.resolve("NIFTY200")     # members ∩ symbols we actually have data for  ← use this
um.sector("Financials")    # sector-wise; um.sectors() lists them
um.coverage("NIFTY50")     # {configured, available, missing}
um.custom(["RELIANCE","ITC"])
```
`members()` = full membership; `resolve()` = intersected with available data, so
research never gets a symbol it can't load. **Config slots:** `NIFTY_NEXT_50`,
`NIFTY200_EXTRA`, `fno` ship empty — fill from an official NSE source, then fetch
their data; the manager works fully regardless. `fetch_data` and
`portfolio_analyzer.SECTOR_MAP` now source from here (backward-compatible —
byte-identical to the old hardcoded values).

## Usage
```python
from data_layer import DataPipeline
p = DataPipeline()                 # or DataPipeline(refresh=True) to re-download
status = p.prepare()               # validates + flags corp-actions; returns a status dict
scores = p.store.scores()          # cached factor scores (symbols × factors)
p.manager.update()                 # incremental top-up — no duplicated downloads
```
CLI: `python data_layer.py` prints version, validation, corp-action flags, the
incremental plan, and available features.

## Tests & artifacts
70 tests total; the data layer adds `test_data_layer.py`, `test_trading_calendar.py`,
`test_data_version.py` (network `update()` is not unit-tested — only its pure planner).
Generated artifacts (`data/_manifest.json`, `data/_feature_cache/`,
`data/_corporate_actions.json`) live under the gitignored `data/`.

## Honest limitations
- Single data source (yfinance daily); no fundamentals, no tick/intraday history.
- Corporate actions are *detected/flagged*, not *computed* (source is pre-adjusted).
- Forward trading-calendar holidays beyond the data are best-effort.
- `update()`'s network path is integration-only (run manually); the decision logic
  it relies on is unit-tested.
