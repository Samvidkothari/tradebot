"""
test_research_pipeline.py — orchestration mechanics (with fake stages, fast).

The real full chain is exercised by the daily job; here we test stage status,
skip, failure-isolation, and the run record — without running every analytic.
"""

import research_pipeline as RP


def test_stage_ok_failed_skip():
    assert RP._run_stage("a", lambda: None)["status"] == "ok"
    f = RP._run_stage("b", lambda: 1 / 0)
    assert f["status"] == "failed" and "ZeroDivisionError" in f["message"]
    assert RP._run_stage("c", lambda: None, skip=True)["status"] == "skipped"


def test_run_with_fake_plan_records_and_skips():
    calls = []
    plan = [("one", lambda: calls.append("one"), False),
            ("two", lambda: calls.append("two"), True),     # skipped
            ("three", lambda: calls.append("three"), False)]
    rec = RP.run(fetch=False, plan=plan)
    assert calls == ["one", "three"]                        # skipped one not called
    assert [s["status"] for s in rec["stages"]] == ["ok", "skipped", "ok"]
    assert rec["ok"] is True and rec["n_ok"] == 2 and rec["n_failed"] == 0
    for k in ("started", "finished", "duration_s", "stages", "ok"):
        assert k in rec


def test_failure_is_isolated_chain_continues():
    plan = [("good", lambda: None, False),
            ("bad", lambda: 1 / 0, False),
            ("after", lambda: None, False)]
    rec = RP.run(fetch=False, plan=plan)
    assert rec["ok"] is False and rec["n_failed"] == 1
    assert rec["stages"][2]["status"] == "ok"               # ran after the failure


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
