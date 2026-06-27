"""
test_trading_calendar.py — tests for the NSE trading calendar (real cached data).
"""

import pandas as pd

from trading_calendar import TradingCalendar


def test_sessions_nonempty_and_sorted():
    cal = TradingCalendar()
    s = cal.sessions()
    assert len(s) > 100
    assert list(s) == sorted(s)


def test_known_session_and_weekend():
    cal = TradingCalendar()
    last = cal.last_session()
    assert cal.is_session(last) is True
    # The Saturday after the last session is never a trading day.
    sat = last + pd.Timedelta(days=(5 - last.weekday()) % 7 or 7)
    assert sat.weekday() == 5
    assert cal.is_session(sat) is False


def test_next_prev_consistency():
    cal = TradingCalendar()
    s = cal.sessions()
    mid = s[len(s) // 2]
    assert cal.prev_session(cal.next_session(mid)) == mid
    assert cal.next_session(s[-1]) is None        # nothing after the last cached session


def test_n_sessions_subrange():
    cal = TradingCalendar()
    s = cal.sessions()
    a, b = s[10], s[20]
    assert cal.n_sessions(a, b) == 11             # inclusive both ends


def test_missing_sessions_returns_list():
    cal = TradingCalendar()
    miss = cal.missing_sessions("RELIANCE")
    assert isinstance(miss, list)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
