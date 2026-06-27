"""
trading_calendar.py — NSE trading calendar.

Ground truth = the dates the NIFTY 50 index actually traded (data/NIFTY50.csv):
the index prints on every NSE session, so `is_session` / `sessions` are EXACT for
the cached history (holidays included automatically, no hand-maintained list to
rot). Weekends are always non-sessions.

Outside the cached range we fall back to a best-effort rule (weekday and not a
fixed national holiday) — a fully accurate FORWARD calendar needs an official NSE
holiday feed, which we don't have. FIXED_HOLIDAYS covers only the reliably-fixed
national holidays; variable-date holidays (Diwali, Holi, …) are captured for the
historical window via the data but not forward. This is stated honestly rather
than shipping a guessed multi-year list.

READ-ONLY; no orders.
"""

from __future__ import annotations

import pandas as pd

import data_io

# Reliably-fixed NSE holidays (same date every year). Variable-date festivals are
# intentionally NOT hard-coded (they'd rot); they're covered historically by the
# data-derived sessions.
FIXED_HOLIDAYS = {(1, 26), (8, 15), (10, 2)}   # Republic Day, Independence, Gandhi Jayanti


class TradingCalendar:
    def __init__(self):
        self._sessions: pd.DatetimeIndex | None = None

    def sessions(self) -> pd.DatetimeIndex:
        """All NSE sessions in the cached history (from the NIFTY index dates)."""
        if self._sessions is None:
            nifty = data_io.load_nifty()
            dts = pd.to_datetime(nifty["date"]).dt.normalize()
            self._sessions = pd.DatetimeIndex(sorted(dts.unique()))
        return self._sessions

    def is_session(self, d) -> bool:
        d = pd.Timestamp(d).normalize()
        s = self.sessions()
        if len(s) and s[0] <= d <= s[-1]:
            return d in s
        return d.weekday() < 5 and (d.month, d.day) not in FIXED_HOLIDAYS

    def session_range(self, start, end) -> pd.DatetimeIndex:
        s = self.sessions()
        return s[(s >= pd.Timestamp(start)) & (s <= pd.Timestamp(end))]

    def n_sessions(self, start, end) -> int:
        return len(self.session_range(start, end))

    def last_session(self, on_or_before=None) -> pd.Timestamp | None:
        s = self.sessions()
        if on_or_before is None:
            return s[-1] if len(s) else None
        sub = s[s <= pd.Timestamp(on_or_before)]
        return sub[-1] if len(sub) else None

    def next_session(self, after) -> pd.Timestamp | None:
        s = self.sessions()
        sub = s[s > pd.Timestamp(after)]
        return sub[0] if len(sub) else None

    def prev_session(self, before) -> pd.Timestamp | None:
        s = self.sessions()
        sub = s[s < pd.Timestamp(before)]
        return sub[-1] if len(sub) else None

    def missing_sessions(self, symbol: str) -> list[str]:
        """Sessions where the index traded but `symbol` has no row (data gaps)."""
        frames = data_io.symbol_frames()
        if symbol not in frames:
            return []
        have = set(pd.to_datetime(frames[symbol]["date"]).dt.normalize())
        return [d.date().isoformat() for d in self.sessions() if d not in have]
