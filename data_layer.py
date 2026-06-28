"""
data_layer.py — unified market-data pipeline for Research.

  MarketDataManager → DataValidator → CorporateActionManager → FeatureStore → Research
  (backed by: trading_calendar, data_version, incremental updates, a feature cache)

A thin, tested orchestration layer over the existing building blocks (fetch_data,
data_io, data_quality, factors). Goals: no duplicated downloads, cached
calculations, fast reloads, clean interfaces. READ-ONLY / research only — no
order-placement code, and it does NOT replace the pre-registered backtests'
direct data_io.load_panel path (those stay byte-identical); this is additive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import data_io
import data_quality
import data_version
import factors as F
import fetch_data
from trading_calendar import TradingCalendar

CA_LOG_PATH = data_io.DATA_DIR / "_corporate_actions.json"


class DataQualityError(RuntimeError):
    """Raised when the panel has FAIL-level data-quality problems."""


# ── 1. MarketDataManager — acquire + load (single source) ─────────────────────

class MarketDataManager:
    """Single entry for market data: fetch (incremental), load (data_io), plus a
    trading calendar and a content version for the current snapshot."""

    def __init__(self, data_dir: Path = data_io.DATA_DIR):
        self.data_dir = Path(data_dir)
        self.calendar = TradingCalendar()
        self._manifest = data_version.DataManifest(self.data_dir)

    # acquisition ------------------------------------------------------------
    def refresh(self, force: bool = True):
        """Full re-download of every symbol (yfinance). Prefer update() for the
        no-duplicate-downloads incremental path."""
        fetch_data.main(refresh=force)
        self._invalidate()

    def update(self, symbols=None, today=None) -> dict:
        """Incremental: fetch only new sessions per symbol (no duplicated
        downloads). Returns a per-symbol summary."""
        summary = IncrementalUpdater(self).update(symbols=symbols, today=today)
        self._invalidate()
        return summary

    def _invalidate(self):
        for fn in (data_io.load_panel, data_io.symbol_frames, data_io.load_nifty):
            fn.cache_clear()
        self.calendar = TradingCalendar()
        self._manifest = data_version.DataManifest(self.data_dir)

    # loading ----------------------------------------------------------------
    def close_panel(self) -> pd.DataFrame:
        return data_io.close_panel()

    def volume_panel(self) -> pd.DataFrame:
        close = data_io.close_panel()
        return data_io.volume_panel(like=close)

    def ohlcv(self, symbol: str):
        return data_io.symbol_frames().get(symbol)

    def nifty(self) -> pd.DataFrame:
        return data_io.load_nifty()

    def universe(self) -> list[str]:
        return list(data_io.close_panel().columns)

    def as_of(self):
        c = data_io.close_panel()
        return c.index[-1].date().isoformat() if len(c) else None

    def context(self) -> F.PanelContext:
        from universe import UniverseManager      # lazy → avoid import cycle
        close = data_io.close_panel()
        nifty = data_io.load_nifty().set_index("date")["close"]
        nifty.index = pd.to_datetime(nifty.index).normalize()
        return F.PanelContext(
            close=close,
            volume=data_io.volume_panel(like=close),
            high=data_io.field_panel("high", like=close),
            low=data_io.field_panel("low", like=close),
            benchmark=nifty.reindex(close.index),
            sectors=UniverseManager().SECTOR_MAP,
        )

    # versioning -------------------------------------------------------------
    @property
    def manifest(self) -> data_version.DataManifest:
        return self._manifest

    @property
    def version(self) -> str:
        return self._manifest.version


# ── 2. DataValidator — quality gate ───────────────────────────────────────────

class DataValidator:
    """Validate the cached panel (wraps data_quality.validate_panel)."""

    def __init__(self, manager: MarketDataManager):
        self.manager = manager
        self._report = None

    def report(self) -> dict:
        if self._report is None:
            self._report = data_quality.validate_panel(self.manager.data_dir)
        return self._report

    def summary(self) -> dict:
        return self.report()["summary"]

    def is_clean(self) -> bool:
        return self.report()["summary"]["FAIL"] == 0

    def failing_symbols(self) -> list[str]:
        return [s["symbol"] for s in self.report()["symbols"] if s["status"] == "FAIL"]

    def usable_symbols(self) -> list[str]:
        return [s["symbol"] for s in self.report()["symbols"] if s["status"] != "FAIL"]

    def assert_usable(self):
        bad = self.failing_symbols()
        if bad:
            raise DataQualityError(f"FAIL-level data for: {', '.join(bad)}")


# ── 3. CorporateActionManager — detection, log, adjustment status ─────────────

class CorporateActionManager:
    """Corporate-action handling.

    HONEST SCOPE: prices come from yfinance with auto_adjust=True, so they are
    ALREADY split/dividend-adjusted (total-return back-adjusted). This manager
    therefore: records that the data is adjusted; DETECTS residual extreme
    single-day jumps that may indicate an *unhandled* action; logs them; and tells
    the incremental updater which symbols to fully re-fetch. It does NOT fabricate
    split ratios (we have no raw feed / actions calendar) — `adjust()` is the seam
    where true back-adjustment would live if such a feed is added.
    """

    SOURCE_AUTO_ADJUSTED = True
    JUMP = 0.20      # |1-day move| above this is flagged as a possible action

    def __init__(self, manager: MarketDataManager):
        self.manager = manager

    @property
    def is_adjusted(self) -> bool:
        return self.SOURCE_AUTO_ADJUSTED

    def detect(self) -> list[dict]:
        """Per-symbol suspect jumps {symbol, date, move} that may be unhandled
        corporate actions."""
        close = self.manager.close_panel()
        events = []
        for sym in close.columns:
            chg = close[sym].pct_change()
            for d, v in chg[chg.abs() > self.JUMP].items():
                events.append({"symbol": sym, "date": d.date().isoformat(),
                               "move": round(float(v), 4)})
        return events

    def flags(self) -> list[dict]:
        """Symbols with at least one suspect jump (compact)."""
        agg: dict[str, int] = {}
        for e in self.detect():
            agg[e["symbol"]] = agg.get(e["symbol"], 0) + 1
        return [{"symbol": s, "suspect_jumps": n} for s, n in sorted(agg.items())]

    def symbols_needing_refetch(self) -> list[str]:
        """Symbols whose latest session shows a suspect jump → likely a fresh
        corporate action; the incremental updater should fully re-fetch them."""
        close = self.manager.close_panel()
        out = []
        for sym in close.columns:
            chg = close[sym].pct_change().dropna()
            if len(chg) and abs(chg.iloc[-1]) > self.JUMP:
                out.append(sym)
        return out

    def log(self, path: Path = CA_LOG_PATH) -> int:
        import json
        events = self.detect()
        Path(path).write_text(json.dumps(
            {"generated": pd.Timestamp.now().isoformat(timespec="seconds"),
             "source_auto_adjusted": self.is_adjusted, "events": events}, indent=2))
        return len(events)

    def adjust(self, panel: pd.DataFrame | None = None):
        """Return the (already-adjusted) close panel + compact flags. The hook for
        true back-adjustment when a raw feed + actions calendar exist."""
        panel = self.manager.close_panel() if panel is None else panel
        return panel, self.flags()


# Backwards-compatible alias.
CorporateActionAdjuster = CorporateActionManager


# ── 4. Feature store — registry, metadata, versioning, cache (feature_store.py) ─
# The store/cache/registry now live in feature_store.py (calculate→store→version→
# reuse). Re-exported here so `from data_layer import FeatureStore` keeps working.
from feature_store import FeatureCache, FeatureRegistry, FeatureStore  # noqa: E402,F401


# ── 5. IncrementalUpdater — no duplicated downloads ───────────────────────────

class IncrementalUpdater:
    """Fetch only NEW sessions per symbol. On a detected corporate-action
    rebasing (yfinance re-adjusted history), full re-fetch that symbol so
    auto-adjusted prices stay internally consistent."""

    REBASE_TOL = 0.01      # >1% change to an already-cached close = adjustment rebase

    def __init__(self, manager: MarketDataManager):
        self.manager = manager

    def plan(self, last_date, today) -> tuple[str, str | None]:
        """PURE: decide what to fetch for one symbol given its last cached session.
        Returns (mode, start) where mode ∈ {full, incremental, uptodate}."""
        if last_date is None:
            return ("full", None)
        nxt = self.manager.calendar.next_session(last_date)
        if nxt is None or nxt > pd.Timestamp(today):
            return ("uptodate", None)
        return ("incremental", nxt.date().isoformat())

    def plan_all(self, today=None) -> dict:
        """PURE (no network): the plan for every symbol from cached data."""
        today = pd.Timestamp(today or pd.Timestamp.today().normalize())
        frames = data_io.symbol_frames()
        out = {}
        for sym, df in frames.items():
            last = pd.to_datetime(df["date"]).max()
            out[sym] = self.plan(last, today)
        return out

    def update(self, symbols=None, today=None) -> dict:
        """NETWORK: apply the plan, appending only new rows (or full re-fetching on
        a corporate-action rebase). Not exercised by the unit tests."""
        import yfinance as yf
        today = pd.Timestamp(today or pd.Timestamp.today().normalize())
        frames = data_io.symbol_frames()
        syms = symbols or list(frames)
        summary = {}
        for sym in syms:
            fp = self.manager.data_dir / f"{sym}.csv"
            df = frames.get(sym)
            last = pd.to_datetime(df["date"]).max() if df is not None else None
            mode, start = self.plan(last, today)
            if mode == "uptodate":
                summary[sym] = {"mode": "uptodate", "added": 0}
                continue
            ticker = "^NSEI" if sym == "NIFTY50" else f"{sym}.NS"
            if mode == "full":
                fetch_data.fetch_symbol(sym, ticker, "2021-06-01",
                                        today.date().isoformat(), refresh=True)
                summary[sym] = {"mode": "full", "added": "all"}
                continue
            # incremental: fetch from the last cached session (overlap) forward
            raw = yf.Ticker(ticker).history(start=str(last.date()),
                                            end=(today + pd.Timedelta(days=1)).date().isoformat(),
                                            interval="1d", auto_adjust=True)
            if raw.empty:
                summary[sym] = {"mode": "uptodate", "added": 0}
                continue
            raw = raw[["Open", "High", "Low", "Close", "Volume"]].copy()
            raw.columns = ["open", "high", "low", "close", "volume"]
            raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
            # corporate-action rebase check on the overlap day
            cached_last = float(df.set_index("date")["close"].iloc[-1])
            if last in raw.index and abs(raw.loc[last, "close"] / cached_last - 1) > self.REBASE_TOL:
                fetch_data.fetch_symbol(sym, ticker, "2021-06-01",
                                        today.date().isoformat(), refresh=True)
                summary[sym] = {"mode": "full (corp-action rebase)", "added": "all"}
                continue
            new = raw[raw.index > last].reset_index().rename(columns={"index": "date", "Date": "date"})
            if new.empty:
                summary[sym] = {"mode": "uptodate", "added": 0}
                continue
            out = pd.concat([df, new[["date", "open", "high", "low", "close", "volume"]]],
                            ignore_index=True)
            out["date"] = pd.to_datetime(out["date"]).dt.normalize()
            out = out.drop_duplicates("date").sort_values("date")
            out.to_csv(fp, index=False)
            summary[sym] = {"mode": "incremental", "added": int(len(new))}
        return summary


# ── 6. DataPipeline — one entry point for Research ────────────────────────────

@dataclass
class DataPipeline:
    """Chains all stages: manager → validator → corporate-actions → feature store."""
    refresh: bool = False

    def __post_init__(self):
        self.manager = MarketDataManager()
        if self.refresh:
            self.manager.refresh()
        self.validator = DataValidator(self.manager)
        self.corporate_actions = CorporateActionManager(self.manager)
        self.store = FeatureStore(self.manager)

    def prepare(self, require_clean: bool = False) -> dict:
        report = self.validator.report()
        if require_clean:
            self.validator.assert_usable()
        flags = self.corporate_actions.flags()
        cal = self.manager.calendar
        return {
            "version": self.manager.version,
            "as_of": self.manager.as_of(),
            "n_symbols": len(self.manager.universe()),
            "n_sessions": len(cal.sessions()),
            "validation": report["summary"],
            "is_clean": self.validator.is_clean(),
            "corporate_actions": {
                "source_auto_adjusted": self.corporate_actions.is_adjusted,
                "flagged_symbols": len(flags),
            },
            "features": list(self.store.factors),
        }


def main():
    p = DataPipeline()
    s = p.prepare()
    W = 66
    print(f"\n{'='*W}\n  DATA PIPELINE  (manager → validator → corp-actions → features)\n{'='*W}")
    print(f"  version {s['version']}   as of {s['as_of']}   "
          f"{s['n_symbols']} symbols / {s['n_sessions']} sessions")
    v = s["validation"]
    print(f"  Validation: {v['OK']} OK · {v['WARN']} WARN · {v['FAIL']} FAIL  "
          f"(clean: {s['is_clean']})")
    ca = s["corporate_actions"]
    print(f"  Corporate actions: auto-adjusted={ca['source_auto_adjusted']}, "
          f"{ca['flagged_symbols']} symbol(s) flagged")
    plan = IncrementalUpdater(p.manager).plan_all()
    modes = {}
    for m, _ in plan.values():
        modes[m] = modes.get(m, 0) + 1
    print(f"  Incremental plan: " + ", ".join(f"{k}={v}" for k, v in modes.items()))
    print(f"  Features: {', '.join(s['features'])}")
    print(f"{'='*W}\n")


if __name__ == "__main__":
    main()
