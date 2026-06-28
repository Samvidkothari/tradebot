"""
test_integration.py — end-to-end smoke tests.

Complements the unit tests by exercising whole pipelines:
  1. the research runners actually produce valid results/*.json with the keys the
     dashboard/templates rely on (catches runner→JSON→template contract drift);
  2. every parameterless dashboard route renders;
  3. the daily digest builds.

No network: the runners read cached data, and the dashboard's live-price / Kite
calls fall back gracefully offline. Side effects are limited to results/*.json
and scratch feature DBs (all gitignored). Runs under pytest or run_tests.py.
"""

import contextlib
import io
import json
from pathlib import Path

import schemas      # single source of truth for the JSON contract

RESULTS = Path(__file__).parent / "results"

# Every parameterless GET route (minus auth endpoints).
ROUTES = [
    "/", "/monitor", "/home", "/pnl", "/paper", "/intraday", "/intraday/compare",
    "/options", "/options/book", "/charts", "/backtests",
    "/tearsheet", "/factors", "/feature-store", "/multi-factor", "/optimizer",
    "/portfolio-analysis", "/risk", "/risk-engine", "/attribution",
    "/data-quality", "/market-intel", "/automation", "/research-assistant",
    "/analytics", "/journal", "/alerts", "/ticket", "/report",
]


def test_research_pipeline_produces_valid_json():
    """Run all research runners; every JSON exists, parses, and has its keys."""
    import refresh_research
    with contextlib.redirect_stdout(io.StringIO()):
        rc = refresh_research.main()
    assert rc == 0, "a research runner failed"
    for fname in schemas.REQUIRED:
        fp = RESULTS / fname
        assert fp.exists(), f"{fname} not produced"
        schemas.validate(fname, json.loads(fp.read_text()))   # raises on drift


def test_all_dashboard_routes_render():
    """Every page renders for an authenticated client (offline-safe)."""
    import dashboard
    c = dashboard.app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
    for route in ROUTES:
        resp = c.get(route)
        assert resp.status_code == 200, f"{route} -> HTTP {resp.status_code}"


def test_login_page_public_and_logout_redirects():
    import dashboard
    c = dashboard.app.test_client()
    assert c.get("/login").status_code == 200          # public
    with c.session_transaction() as s:
        s["authed"] = True
    assert c.get("/logout").status_code == 302          # redirects to login


def test_digest_builds():
    import digest
    d = digest.build_digest()
    for k in ("generated", "lowvol", "options", "condor"):
        assert k in d, f"digest missing '{k}'"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
