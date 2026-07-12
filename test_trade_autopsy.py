"""
test_trade_autopsy.py — the post-trade autopsy packet (Pillar 5).

Checks the markdown packet contains the required sections and correct win/loss +
process verdict, that a coaching prompt bundles multiple trades, and that missing
fields degrade gracefully. Pure formatting.
"""
import trade_autopsy as TA
import trade_journal as TJ
import pandas as pd


def _rec(gross, adherence=True, setup="ep"):
    raw = {"symbol": "AAA", "side": "long", "entry_date": "2024-01-03",
           "exit_date": pd.Timestamp("2024-01-08"), "entry": 100.0,
           "exit": 100.0 * (1 + gross), "sl": 95.0, "risk": 0.05,
           "gross_ret": gross, "reason": "trail"}
    return TJ.enrich(raw, setup_type=setup, cost=0.003, regime="bull",
                     rule_adherence=adherence)


def test_markdown_has_required_sections():
    md = TA.autopsy_markdown(_rec(0.10), slippage=0.0005)
    for section in ("Execution", "Cost & slippage", "Rule adherence",
                    "Chart coordinates"):
        assert section in md
    assert "WIN" in md and "bull" in md


def test_loss_and_process_error_flagged():
    md = TA.autopsy_markdown(_rec(-0.06, adherence=False))
    assert "LOSS" in md
    assert "BAD PROCESS" in md and "❌" in md


def test_valid_loss_distinguished_from_bad_process():
    md = TA.autopsy_markdown(_rec(-0.06, adherence=True))
    assert "valid outcome" in md and "LOSS" in md


def test_coaching_prompt_bundles_trades():
    trades = [_rec(0.10), _rec(-0.05, adherence=False), _rec(0.03)]
    p = TA.coaching_prompt(trades, context="weekly review")
    assert p.count("Trade Autopsy") == 3
    assert "process coach" in p.lower() and "weekly review" in p


def test_session_autopsy_header_stats():
    trades = [_rec(0.10), _rec(-0.05, adherence=False), _rec(0.02)]
    doc = TA.session_autopsy(trades, title="Week 27")
    assert "Week 27 — 3 trades" in doc
    assert "process errors 1" in doc


def test_missing_fields_graceful():
    md = TA.autopsy_markdown({"setup_type": "x", "symbol": "Z", "side": "long"})
    assert "Trade Autopsy" in md and "—" in md            # dashes for absent numbers
