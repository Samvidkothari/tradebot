"""
data_io.py — Single source of truth for loading cached price/volume data.

Before this module, CSV loading was copy-pasted across backtest_lowvol.py,
backtest_momentum.py (byte-identical load_panel), factor_report.py,
portfolio_analyzer.py, and data_quality.py. This centralises it.

Pure I/O — reads data/*.csv (written by fetch_data.py); no network, no strategy
logic, no orders. NOTE: this loads DATA only; it does not change any pre-registered
strategy rule. `load_panel` is a literal copy of the backtests' original loader,
so its output is byte-identical (proven by the regression tests + unchanged
backtest verdicts).

Functions:
  load_panel()        -> (close_panel_ex_index, nifty_df)   # backtests' contract
  load_nifty()        -> NIFTY 50 benchmark DataFrame (date/close)
  symbol_frames()     -> {symbol: full OHLCV DataFrame}     # excludes the index
  close_panel(syms)   -> close DataFrame (all ex-index, or a subset)
  volume_panel(...)   -> volume DataFrame (optionally aligned to a close panel)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA_DIR     = Path(__file__).parent / "data"
INDEX_SYMBOL = "NIFTY50"


def _read(fp: Path) -> pd.DataFrame:
    return pd.read_csv(fp, parse_dates=["date"]).sort_values("date")


def load_panel(data_dir: Path = DATA_DIR):
    """Build a daily-close panel from every stock CSV except the index.
    Returns (panel_raw, nifty_df). LITERAL copy of the backtests' original loader
    — output is byte-identical, so the pre-registered verdicts are unaffected."""
    closes = {}
    nifty_df = None
    for fp in sorted(data_dir.glob("*.csv")):
        sym = fp.stem
        df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
        if sym == INDEX_SYMBOL:
            nifty_df = df.reset_index(drop=True)
            continue
        closes[sym] = df.set_index("date")["close"]

    if nifty_df is None:
        raise FileNotFoundError("data/NIFTY50.csv not found — run fetch_data.py")

    panel_raw = pd.DataFrame(closes).sort_index()
    return panel_raw, nifty_df


def load_nifty(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    fp = data_dir / f"{INDEX_SYMBOL}.csv"
    if not fp.exists():
        raise FileNotFoundError("data/NIFTY50.csv not found — run fetch_data.py")
    return _read(fp).reset_index(drop=True)


def symbol_frames(data_dir: Path = DATA_DIR, exclude_index: bool = True) -> dict:
    """All per-symbol OHLCV frames, keyed by symbol (index excluded by default)."""
    out = {}
    for fp in sorted(data_dir.glob("*.csv")):
        if exclude_index and fp.stem == INDEX_SYMBOL:
            continue
        out[fp.stem] = _read(fp).reset_index(drop=True)
    return out


def close_panel(symbols=None, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Daily-close panel (cols = symbols). `symbols=None` → all ex-index names."""
    frames = symbol_frames(data_dir)
    if symbols is not None:
        frames = {s: frames[s] for s in symbols if s in frames}
    return pd.DataFrame(
        {s: df.set_index("date")["close"] for s, df in frames.items()}).sort_index()


def volume_panel(symbols=None, data_dir: Path = DATA_DIR, like: pd.DataFrame = None):
    """Daily-volume panel; if `like` is given, reindexed to its rows."""
    frames = symbol_frames(data_dir)
    if symbols is not None:
        frames = {s: frames[s] for s in symbols if s in frames}
    vp = pd.DataFrame(
        {s: df.set_index("date")["volume"] for s, df in frames.items()
         if "volume" in df.columns}).sort_index()
    return vp.reindex(like.index) if (like is not None and not vp.empty) else vp
