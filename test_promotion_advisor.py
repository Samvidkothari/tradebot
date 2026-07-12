"""test_promotion_advisor.py — the lifecycle automation must be conservative."""

import promotion_advisor as pa

RULES = {"auto_execute": False, "max_changes_per_run": 1,
         "min_oos_days": 120, "max_oos_drawdown": -0.20, "min_oos_sharpe": 0.0,
         "min_walk_forward_positive_frac": 0.6, "min_closed_cycles": 5}

GOOD_EQ = {"oos": {"n_days": 600, "max_drawdown": -0.12, "sharpe": 0.4,
                   "total_return": 0.18},
           "walk_forward": [{"cagr": 0.05}, {"cagr": 0.08}, {"cagr": 0.02}]}
BAD_EQ = {"oos": {"n_days": 600, "max_drawdown": -0.31, "sharpe": -0.2,
                  "total_return": -0.08},
          "walk_forward": [{"cagr": -0.05}, {"cagr": 0.01}, {"cagr": -0.02}]}
YOUNG_EQ = {"oos": {"n_days": 40}, "walk_forward": []}


def _verdicts(strategies, flags, rules=RULES):
    return {d["book"]: d for d in pa.evaluate(strategies, rules, flags)}


def test_keep_promote_retire_watch():
    v = _verdicts({"lowvol": GOOD_EQ, "momentum": BAD_EQ, "fresh": YOUNG_EQ},
                  {"lowvol": True, "momentum": True})
    assert v["lowvol"]["verdict"] == "KEEP"
    assert v["momentum"]["verdict"] == "RETIRE"
    assert "drawdown" in v["momentum"]["reason"]
    assert v["fresh"]["verdict"] == "WATCH"


def test_disabled_passing_book_is_promote_candidate():
    v = _verdicts({"momentum": GOOD_EQ}, {"momentum": False})
    assert v["momentum"]["verdict"] == "PROMOTE"


def test_options_book_needs_closed_cycles():
    strat = {"strangle": {"kind": "options", "n_closed": 0, "realised": 0.0,
                          "unrealised": 4310.0}}
    v = _verdicts(strat, {"strangle": True})
    assert v["strangle"]["verdict"] == "WATCH"
    strat["strangle"].update(n_closed=8, realised=-9000.0, unrealised=0.0)
    v = _verdicts(strat, {"strangle": True})
    assert v["strangle"]["verdict"] == "RETIRE"


def test_advise_only_never_touches_flags():
    decisions = pa.evaluate({"momentum": BAD_EQ}, RULES, {"momentum": True})
    calls = []
    actions = pa.apply(decisions, RULES, set_enabled=lambda k, e: calls.append((k, e)))
    assert actions == [] and calls == []          # auto_execute is False


def test_auto_execute_caps_changes_per_run():
    rules = dict(RULES, auto_execute=True, max_changes_per_run=1)
    strategies = {"momentum": BAD_EQ, "lowvol": BAD_EQ}
    decisions = pa.evaluate(strategies, rules, {"momentum": True, "lowvol": True})
    calls = []
    actions = pa.apply(decisions, rules, set_enabled=lambda k, e: calls.append((k, e)))
    assert len(actions) == 1 and len(calls) == 1  # budget of one change per day
    assert calls[0][1] is False                   # and it disabled, not enabled


def test_rules_file_is_loadable_and_conservative_by_default():
    rules = pa.load_rules()
    assert rules["auto_execute"] is False
    assert rules["max_changes_per_run"] >= 1
