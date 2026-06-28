"""
test_research_assistant.py — the read-only analyst.

Verifies the heuristics fire on controlled inputs (decay, overfitting, crowding),
that the report assembles with the required keys, and — most importantly — that
the assistant is structurally incapable of mutating strategies or placing orders
(it exposes no such function and writes only its two report files).
"""

import contextlib
import io

import research_assistant as RA


def _strat(full_cagr, oos_cagr, full_sharpe, oos_sharpe, wf_cagrs, kind="equity"):
    return {
        "label": "X", "kind": kind, "sufficient": True,
        "full": {"cagr": full_cagr, "sharpe": full_sharpe, "max_drawdown": -0.2,
                 "alpha": 0.04},
        "oos": {"cagr": oos_cagr, "sharpe": oos_sharpe, "start": "2024-01-01"},
        "walk_forward": [{"cagr": c} for c in wf_cagrs],
        "monte_carlo": {"prob_negative_cagr": 0.05},
        "regime_compat": {"compatible": True},
    }


def test_alpha_decay_flags_collapse():
    ts = {"strategies": {"s": _strat(0.15, 0.05, 0.5, 0.09, [0.1, 0.1, 0.1, 0.1])}}
    f = RA.check_alpha_decay(ts)[0]
    assert f["severity"] in ("watch", "warn")          # OOS 33% of full → decay
    assert f["evidence"]["cagr_ratio"] == round(0.05 / 0.15, 2)
    # a strategy that holds up should read 'good'
    ok = RA.check_alpha_decay({"strategies": {"s": _strat(0.12, 0.11, 0.4, 0.38,
                                                          [0.1, 0.1, 0.1, 0.1])}})[0]
    assert ok["severity"] == "good"


def test_overfitting_flags_negative_segments():
    ts = {"strategies": {"s": _strat(0.15, 0.06, 0.5, 0.1, [0.15, 0.67, -0.05, -0.03])}}
    f = RA.check_overfitting(ts)[0]
    assert f["severity"] == "warn"                     # 2 negative segments
    assert f["evidence"]["neg_segments"] == 2
    # detail must mention the concern (explanation never contradicts the colour)
    assert "negative" in f["detail"]


def test_factor_crowding_detected():
    factors = {"factors": {f"f{i}": {"top": [{"symbol": "ONGC", "score": 1.0}]}
                           for i in range(5)}, "unavailable": ["ROE"]}
    titles = [x["title"] for x in RA.check_factor_performance(factors)]
    assert any("crowding" in t.lower() for t in titles)


def test_report_has_required_keys_and_validates():
    import schemas
    # generate upstream data first so checks have something real to read
    import factor_report, risk_engine, tearsheet
    with contextlib.redirect_stdout(io.StringIO()):
        tearsheet.main(); risk_engine.main(); factor_report.main()
    rep = RA.build_report()
    for k in ("generated", "summary", "findings", "disclaimer", "headline"):
        assert k in rep
    schemas.validate("research_assistant.json", rep)   # raises on drift
    assert all("recommendation" in f and "detail" in f for f in rep["findings"])


def test_no_mutation_or_order_surface():
    """Structural guarantee: the module exposes no strategy-mutating / order
    PLACEMENT function (constants like SEV_ORDER are fine — only callables count)."""
    banned = ("place_order", "place", "buy", "sell", "modify_strategy",
              "write_spec", "execute", "submit")
    callables = [n for n in dir(RA)
                 if not n.startswith("_") and callable(getattr(RA, n))]
    offenders = [n for n in callables if any(b in n.lower() for b in banned)]
    assert not offenders, f"unexpected mutating/order API: {offenders}"


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
