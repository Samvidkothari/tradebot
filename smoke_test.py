#!/usr/bin/env python3
"""
smoke_test.py — fast regression guard for the dashboard.

Boots the Flask app in-process (no running server needed), authenticates a test
client, and GETs every route — parameterless and parameterized — asserting each
returns 200 (or a 200 after following redirects). Exits non-zero if any page
errors, so a "new template + stale code" skew (or any other breakage) fails
loudly instead of silently 500-ing a page in the browser.

Usage:
    python smoke_test.py          # stubs live price fetches (fast, offline)
    python smoke_test.py --live   # use real yfinance price marks

It is strictly READ-ONLY: it never posts forms, places orders, or writes to any
trading ledger. Feature DBs (journal/alerts/orders) are only opened read-style.
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LIVE = "--live" in sys.argv

import dashboard          # noqa: E402  (imports the configured app + blueprints)
import web_common         # noqa: E402

if not LIVE:
    # Avoid network: fall back to the latest CSV close instead of hitting yfinance.
    _stub = lambda *a, **k: None
    dashboard.fetch_live = _stub
    web_common.fetch_live = _stub

app = dashboard.app
app.testing = True


def _authed_client():
    c = app.test_client()
    with c.session_transaction() as s:
        s["authed"] = True
    return c


def _sample_args():
    """Concrete values for routes that take a path parameter, pulled from real
    on-disk data so the parameterized routes are actually exercised."""
    args = {"symbol": [], "name": [], "strategy": []}
    data_dir = os.path.join(HERE, "data")
    if os.path.isdir(data_dir):
        args["symbol"] = sorted(f[:-4] for f in os.listdir(data_dir)
                                if f.endswith(".csv"))[:2]
    res_dir = os.path.join(HERE, "results")
    if os.path.isdir(res_dir):
        args["name"] = sorted(f for f in os.listdir(res_dir) if f.endswith(".md"))[:1]
    idb = os.path.join(HERE, "intraday.db")
    if os.path.exists(idb):
        try:
            cc = sqlite3.connect(f"file:{idb}?mode=ro", uri=True)
            args["strategy"] = [r[0] for r in cc.execute("SELECT strategy FROM account")]
            cc.close()
        except Exception:
            pass
    return args


def collect_urls():
    """Every GET URL worth checking: parameterless rules verbatim, parameterized
    rules expanded with sample args, plus key query-string variants."""
    args = _sample_args()
    urls = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint == "static":
            continue
        if not rule.arguments:
            urls.append(rule.rule)
            continue
        # expand single-arg path routes (e.g. /charts/<symbol>, /backtests/<name>)
        if len(rule.arguments) == 1:
            (arg,) = tuple(rule.arguments)
            for val in args.get(arg, []):
                urls.append(rule.rule.replace(f"<{arg}>", str(val))
                                     .replace(f"<path:{arg}>", str(val)))
    # query-string variants for the strategy-aware pages
    for strat in args.get("strategy", []):
        urls += [f"/intraday?strategy={strat}", f"/analytics?strategy={strat}"]
    # de-dupe, keep order
    seen, out = set(), []
    for u in urls:
        if "<" in u:           # skip any rule we couldn't fill
            continue
        if u not in seen:
            seen.add(u); out.append(u)
    return sorted(out)


def main():
    c = _authed_client()
    urls = collect_urls()
    failures = []
    for u in urls:
        # /logout clears the session, so re-auth before each request
        with c.session_transaction() as s:
            s["authed"] = True
        try:
            rv = c.get(u, follow_redirects=True)
            if rv.status_code != 200:
                tail = " ".join(rv.get_data(as_text=True).splitlines()[-2:])[:160]
                failures.append((u, rv.status_code, tail))
                print(f"FAIL {rv.status_code}  {u}")
            else:
                print(f"ok   200  {u}")
        except Exception as e:
            failures.append((u, "EXC", str(e)[:160]))
            print(f"FAIL EXC  {u} :: {type(e).__name__}: {e}")

    print(f"\n{len(urls)} routes checked, {len(failures)} failure(s)"
          f"{' (prices stubbed; pass --live for real marks)' if not LIVE else ''}")
    for u, code, info in failures:
        print(f"  ✗ {code}  {u}  | {info}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
