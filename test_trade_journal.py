"""
test_trade_journal.py — trade-metadata schema/logger + pattern isolation (Pillar 3).

Checks enrichment (R math, day-of-week, hold, net-of-cost), CSV round-trip with
dedupe, and that the decay parser actually flags a faked fading edge and a
negative bucket. Pure data; a temp CSV under tmp_path.
"""
import numpy as np
import pandas as pd
import pytest

import trade_journal as TJ
import pattern_isolation as PI


def _raw(entry_date, gross, risk=0.05, symbol="AAA", side="long"):
    return {"symbol": symbol, "side": side, "entry_date": entry_date,
            "exit_date": pd.Timestamp(entry_date) + pd.Timedelta(days=5),
            "entry": 100.0, "exit": 100.0 * (1 + gross), "sl": 95.0,
            "risk": risk, "gross_ret": gross, "reason": "trail"}


def test_enrich_math_and_metadata():
    rec = TJ.enrich(_raw("2024-01-03", gross=0.10, risk=0.05), setup_type="ep",
                    cost=0.003, regime="bull")
    assert rec.net_ret == pytest.approx(0.097)
    assert rec.R == pytest.approx(0.097 / 0.05)
    assert rec.dow == "Wed"                      # 2024-01-03 is a Wednesday
    assert rec.month == "2024-01" and rec.quarter == "2024-Q1"
    assert rec.hold_bars == 5 and rec.setup_type == "ep" and rec.regime == "bull"


def test_zero_risk_safe():
    rec = TJ.enrich(_raw("2024-02-01", gross=0.02, risk=0.0), setup_type="x")
    assert rec.R == 0.0                          # no divide-by-zero


def test_csv_append_and_dedupe(tmp_path):
    recs = [TJ.enrich(_raw(f"2024-03-0{i}", gross=0.01 * i), setup_type="ep")
            for i in range(1, 6)]
    p = tmp_path / "log.csv"
    TJ.append_csv(recs, p)
    TJ.append_csv(recs, p)                       # same ids again → must dedupe
    df = pd.read_csv(p)
    assert len(df) == 5
    assert set(df["setup_type"]) == {"ep"}


def test_to_frame_sorted_by_exit():
    recs = [TJ.enrich(_raw("2024-05-10", 0.01), setup_type="a"),
            TJ.enrich(_raw("2024-04-10", 0.01), setup_type="a")]
    df = TJ.to_frame(recs)
    assert list(df["exit_date"]) == sorted(df["exit_date"])


def _log(setups_rets):
    """Build a trade DataFrame from (setup, entry_date, gross, risk) tuples."""
    recs = [TJ.enrich(_raw(d, g, risk=rk), setup_type=s, cost=0.0)
            for (s, d, g, rk) in setups_rets]
    return TJ.to_frame(recs)


def test_decay_flag_fires_when_recent_turns_negative():
    # 40 winners then 30 losers for setup 'fade' → full>0 but recent<0
    rows = []
    base = pd.Timestamp("2023-01-02")
    for i in range(40):
        rows.append(("fade", (base + pd.Timedelta(days=i)).date().isoformat(), 0.05, 0.05))
    for i in range(40, 70):
        rows.append(("fade", (base + pd.Timedelta(days=i)).date().isoformat(), -0.05, 0.05))
    df = _log(rows)
    rep = PI.weekly_report(df)
    scopes = {f["scope"] for f in rep["decay_flags"]}
    assert "ALL" in scopes or "setup:fade" in scopes


def test_negative_bucket_flagged():
    # a losing day-of-week style bucket: 25 losers all same setup
    base = pd.Timestamp("2023-06-05")            # a Monday
    rows = [("x", (base + pd.Timedelta(weeks=i)).date().isoformat(), -0.03, 0.05)
            for i in range(25)]
    df = _log(rows)
    rep = PI.weekly_report(df)
    assert rep["bucket_flags"], "expected at least one negative-expectancy bucket"
    assert any(f["expectancy"] < 0 for f in rep["bucket_flags"])


def test_healthy_book_has_no_flags():
    base = pd.Timestamp("2023-01-02")
    rows = [("good", (base + pd.Timedelta(days=i)).date().isoformat(), 0.04, 0.05)
            for i in range(60)]
    rep = PI.weekly_report(_log(rows))
    assert rep["decay_flags"] == [] and rep["bucket_flags"] == []
