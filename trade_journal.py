"""trade_journal.py — standardized trade-metadata schema + logger (Pillar 3).

The existing `portfolio.db fills` logs P&L but no *context*. This adds the
metadata layer the Operational Edge Framework needs for pattern isolation: every
closed trade is recorded with its SETUP TYPE, REGIME at entry, DAY-OF-WEEK, month,
holding period, R multiple, costs, and a rule-adherence flag. Backtests and the
paper trader emit trade dicts (entry/exit/side/reason/risk/gross_ret); `enrich()`
turns those into a uniform `TradeRecord`, and `append_csv()` grows a tidy log that
`pattern_isolation.py` parses for edge decay.

Daily-bar honesty: intraday hour/minute blocks are N/A here, so the time axes are
day-of-week, month/quarter, and holding-period buckets — the daily-bar analog of
the framework's "when does the edge disappear" question. Pure data; no orders.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class TradeRecord:
    """One closed trade, fully tagged. `setup_type` and `regime` are the isolation
    keys; R is P&L in units of initial risk (the comparable metric across sleeves)."""
    trade_id: str
    setup_type: str                 # e.g. 'episodic_pivot', 'lowvol', 'priceaction'
    symbol: str
    side: str                       # 'long' / 'short'
    entry_date: str                 # ISO date/datetime
    exit_date: str
    entry: float
    exit: float
    stop: float
    risk: float                     # (entry-stop)/entry — the R denominator
    gross_ret: float                # before costs
    cost: float                     # round-trip cost fraction charged
    net_ret: float                  # gross_ret - cost
    R: float                        # net_ret / risk
    exit_reason: str                # 'trail'/'stop'/'target'/'time'/'expiry'...
    regime: str = ""                # regime tag at entry (from regime.classify)
    dow: str = ""                   # day-of-week of entry (Mon..Sun)
    month: str = ""                 # YYYY-MM of entry
    quarter: str = ""               # YYYY-Qn of entry
    hold_bars: int = 0              # trading days held (if computable)
    rule_adherence: bool = True     # did the trade follow its pre-defined rules?
    note: str = ""


def _to_ts(x):
    return pd.Timestamp(x)


def enrich(trade: dict, setup_type: str, cost: float = 0.0,
           regime: str = "", rule_adherence: bool = True,
           note: str = "", trade_id: str | None = None) -> TradeRecord:
    """Turn a raw backtest/paper trade dict into a fully-tagged TradeRecord.

    `trade` must have: entry_date, exit_date, side, entry, exit, risk, gross_ret
    (the shape emitted by episodic_pivot / priceaction / trailing_exit). `cost` is
    the round-trip cost fraction to charge; net and R are derived."""
    ed, xd = _to_ts(trade["entry_date"]), _to_ts(trade["exit_date"])
    risk = float(trade.get("risk") or 0.0)
    gross = float(trade["gross_ret"])
    net = gross - cost
    R = (net / risk) if risk > 0 else 0.0
    hold = int(max(0, (xd.normalize() - ed.normalize()).days))
    tid = trade_id or f"{setup_type}:{trade.get('symbol','?')}:{ed.date()}"
    return TradeRecord(
        trade_id=tid, setup_type=setup_type, symbol=str(trade.get("symbol", "")),
        side=str(trade.get("side", "long")), entry_date=ed.isoformat(),
        exit_date=xd.isoformat(), entry=float(trade["entry"]),
        exit=float(trade.get("exit", trade["entry"])),
        stop=float(trade.get("sl", trade.get("stop", 0.0))),
        risk=risk, gross_ret=gross, cost=float(cost), net_ret=net, R=R,
        exit_reason=str(trade.get("reason", trade.get("exit_reason", ""))),
        regime=regime, dow=DOW_NAMES[ed.dayofweek], month=f"{ed.year}-{ed.month:02d}",
        quarter=f"{ed.year}-Q{(ed.month - 1)//3 + 1}", hold_bars=hold,
        rule_adherence=bool(rule_adherence), note=note)


def to_frame(records) -> pd.DataFrame:
    """DataFrame of TradeRecords (or dicts), with typed dates, sorted by exit."""
    rows = [asdict(r) if isinstance(r, TradeRecord) else dict(r) for r in records]
    df = pd.DataFrame(rows, columns=[f.name for f in fields(TradeRecord)])
    if not df.empty:
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        df = df.sort_values("exit_date").reset_index(drop=True)
    return df


def append_csv(records, path: str | Path) -> Path:
    """Append records to a CSV log (creating it with a header if absent). Returns
    the path. This is the durable trade log pattern_isolation.py reads."""
    path = Path(path)
    new = to_frame(records)
    if path.exists():
        old = pd.read_csv(path)
        out = pd.concat([old, new], ignore_index=True)
        # normalize exit_date so string (from CSV) and Timestamp (fresh) keys match
        out["exit_date"] = pd.to_datetime(out["exit_date"])
        out = out.drop_duplicates(subset=["trade_id", "exit_date"], keep="last")
    else:
        out = new
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path
