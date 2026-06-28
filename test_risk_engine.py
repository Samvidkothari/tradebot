"""
test_risk_engine.py — tests for the risk monitor (limit checks + emergency logic).
"""

import risk_engine as RE
from risk_engine import RiskEngine, RiskLimits, _check


def test_check_loss_breach_when_below():
    assert _check(-0.05, -0.03, hard=True)["status"] == "BREACH"   # -5% <= -3% limit
    assert _check(-0.01, -0.03, hard=True)["status"] == "OK"
    assert _check(None, -0.03, hard=True)["status"] == "n/a"


def test_check_exposure_breach_when_above():
    assert _check(0.40, 0.35, hard=False, breach_when_below=False)["status"] == "BREACH"
    assert _check(0.20, 0.35, hard=False, breach_when_below=False)["status"] == "OK"


def test_config_loads():
    L = RiskLimits.from_config()
    assert L.daily_loss_limit < 0 and L.max_drawdown_limit < 0
    assert 0 < L.sector_limit <= 1 and 0 < L.atr_risk_pct < 1


def test_evaluate_real_book_shape_and_status():
    r = RiskEngine(RiskLimits.from_config()).evaluate()
    for k in ("status", "emergency", "reason", "checks", "atr_sizing", "note"):
        assert k in r
    assert r["status"] in ("OK", "WARN", "EMERGENCY")
    assert set(r["checks"]) == {"daily_loss", "max_drawdown", "sector_exposure", "correlation"}
    # emergency iff a HARD check breached
    hard_breach = any(c["hard"] and c["status"] == "BREACH" for c in r["checks"].values())
    assert r["emergency"] == hard_breach


def test_tight_limits_trigger_emergency():
    # impossible-to-pass drawdown limit → hard breach → EMERGENCY
    L = RiskLimits.from_config()
    L.max_drawdown_limit = 0.0           # any drawdown <= 0 breaches
    r = RiskEngine(L).evaluate()
    if r["checks"]["max_drawdown"]["value"] is not None:
        assert r["checks"]["max_drawdown"]["status"] == "BREACH"
        assert r["emergency"] is True and r["status"] == "EMERGENCY"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
