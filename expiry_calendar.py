"""
expiry_calendar.py — NSE F&O expiry calendar (weekly + monthly), holiday-adjusted.

NSE index options expire on **Thursday** (weekly), and the **last Thursday** of the
month is the monthly expiry; if that Thursday is an NSE holiday, expiry rolls back
to the previous trading session. Holiday adjustment uses the TradingCalendar
(sessions derived from the NIFTY index — accurate over the cached history,
best-effort weekday rule beyond it).

READ-ONLY; no orders. (NSE has shifted weekly weekdays over time; this uses the
long-standing Thursday convention — `WEEKLY_WEEKDAY` is the single knob.)
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

import pandas as pd

from trading_calendar import TradingCalendar

WEEKLY_WEEKDAY = 3      # Thursday


class ExpiryCalendar:
    def __init__(self, calendar: TradingCalendar | None = None):
        self.cal = calendar or TradingCalendar()

    def _adjust(self, d) -> pd.Timestamp:
        """Roll back to the previous trading session if `d` isn't one (NSE moves a
        holiday expiry to the prior session)."""
        d = pd.Timestamp(d).normalize()
        for _ in range(10):
            if self.cal.is_session(d):
                return d
            d -= pd.Timedelta(days=1)
        return d

    @staticmethod
    def _last_thursday(year: int, month: int) -> date:
        last = _cal.monthrange(year, month)[1]
        d = date(year, month, last)
        return d - timedelta(days=(d.weekday() - WEEKLY_WEEKDAY) % 7)

    def monthly_expiry(self, year: int, month: int) -> pd.Timestamp:
        return self._adjust(self._last_thursday(year, month))

    def next_monthly(self, on=None) -> pd.Timestamp:
        on = pd.Timestamp(on or pd.Timestamp.today()).normalize()
        e = self.monthly_expiry(on.year, on.month)
        if e < on:
            y, m = (on.year + (on.month == 12)), (on.month % 12 + 1)
            e = self.monthly_expiry(y, m)
        return e

    def next_weekly(self, on=None) -> pd.Timestamp:
        on = pd.Timestamp(on or pd.Timestamp.today()).normalize()
        # the Thursday of this week, or next week's if already past
        thu = on + pd.Timedelta(days=(WEEKLY_WEEKDAY - on.weekday()) % 7)
        e = self._adjust(thu)
        if e < on:
            e = self._adjust(thu + pd.Timedelta(days=7))
        return e

    def weekly_expiries(self, start, end) -> list[pd.Timestamp]:
        start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        thu = start + pd.Timedelta(days=(WEEKLY_WEEKDAY - start.weekday()) % 7)
        out = []
        while thu <= end:
            out.append(self._adjust(thu))
            thu += pd.Timedelta(days=7)
        return out

    def is_monthly_expiry(self, d) -> bool:
        d = pd.Timestamp(d).normalize()
        return d == self.monthly_expiry(d.year, d.month)

    def days_to(self, expiry, on=None) -> int:
        on = pd.Timestamp(on or pd.Timestamp.today()).normalize()
        return int((pd.Timestamp(expiry).normalize() - on).days)
