"""
test_expiry_calendar.py — NSE expiry calendar (weekly / monthly, holiday-adjusted).
"""

import pandas as pd

from expiry_calendar import ExpiryCalendar


def test_monthly_is_last_thursday_or_earlier():
    e = ExpiryCalendar()
    # June 2026: last Thursday is the 25th.
    m = e.monthly_expiry(2026, 6)
    assert m <= pd.Timestamp("2026-06-25")
    assert m.weekday() <= 3                        # Thu or rolled back earlier


def test_next_weekly_is_thursdayish_and_future():
    e = ExpiryCalendar()
    on = pd.Timestamp("2026-06-15")                # a Monday
    w = e.next_weekly(on)
    assert w >= on
    assert (w - on).days <= 7
    assert w.weekday() <= 3                        # Thursday or rolled back


def test_weekly_expiries_span_and_spacing():
    e = ExpiryCalendar()
    ws = e.weekly_expiries("2026-06-01", "2026-06-30")
    assert len(ws) >= 4                            # ~4-5 Thursdays in a month
    assert all(w.weekday() <= 3 for w in ws)


def test_next_monthly_rolls_to_next_month_when_past():
    e = ExpiryCalendar()
    # On the 28th, June's expiry (25th) has passed → next monthly is in July.
    nm = e.next_monthly(pd.Timestamp("2026-06-28"))
    assert nm.month == 7 and nm.year == 2026


def test_days_to():
    e = ExpiryCalendar()
    assert e.days_to("2026-06-25", on="2026-06-20") == 5


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
