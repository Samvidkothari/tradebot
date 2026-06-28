"""
test_research_summary.py — the daily markdown brief.

Generates the upstream JSONs first (fast runners) so the summary has real data,
then checks every section renders and the research-only disclaimer is present.
"""

import contextlib
import io

import research_summary as RS


def _ensure_data():
    import factor_report, risk_engine, tearsheet
    with contextlib.redirect_stdout(io.StringIO()):
        tearsheet.main()
        risk_engine.main()
        factor_report.main()


def test_build_summary_has_all_sections():
    _ensure_data()
    md = RS.build_summary()
    for h in ("# Daily Research Summary", "## Market regime",
              "## Strategy performance", "## Risk", "## Top factor signals"):
        assert h in md, f"missing section: {h}"
    assert "Monte Carlo" in md and "Walk-forward" in md
    assert "places a live order" in md            # discipline disclaimer


def test_pct_and_num_helpers():
    assert RS._pct(0.111, signed=True) == "+11.1%"
    assert RS._pct(0.159) == "15.9%"
    assert RS._pct(None) == "—"
    assert RS._num(0.4) == "0.40"
    assert RS._num(None) == "—"


def test_load_missing_is_none():
    assert RS._load("definitely_not_a_real_file_xyz.json") is None


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
