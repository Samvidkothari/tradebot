"""
test_routes.py — web-layer regression tests (the part that previously shipped 500s).

Boots the Flask app in-process and asserts every route renders, auth is enforced,
and the login comparison accepts/rejects correctly. Reuses smoke_test's URL
collection so the route list stays in one place. Runs under the existing pytest
config; READ-ONLY (no form posts beyond /login, no orders, no ledger writes).
"""

import smoke_test
from smoke_test import app, collect_urls


def _authed():
    c = app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
    return c


def test_all_routes_render_200():
    c = _authed()
    failures = []
    for url in collect_urls():
        with c.session_transaction() as s:   # /logout clears the session
            s["authed"] = True
        rv = c.get(url, follow_redirects=True)
        if rv.status_code != 200:
            failures.append((url, rv.status_code))
    assert not failures, f"non-200 routes: {failures}"


def test_protected_route_redirects_when_unauthenticated():
    c = app.test_client()
    rv = c.get("/")            # the overview is login-protected
    assert rv.status_code == 302
    assert "/login" in rv.headers.get("Location", "")


def test_login_rejects_wrong_and_accepts_right(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "unit-test-pw-123")
    c = app.test_client()
    bad = c.post("/login", data={"password": "not-it"})
    assert "Wrong password" in bad.get_data(as_text=True)
    good = c.post("/login", data={"password": "unit-test-pw-123"})
    assert good.status_code == 302
    assert "/home" in good.headers.get("Location", "")
