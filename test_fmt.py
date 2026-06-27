"""
test_fmt.py — unit tests for the shared display formatters (fmt.py).

Locks in parity with the per-runner helpers they replaced.
"""

import fmt


def test_val_number_and_percent_and_none():
    assert fmt.val(1.2345) == "1.23"
    assert fmt.val(0.0123, pct=True) == "+1.23%"
    assert fmt.val(-0.05, pct=True) == "-5.00%"
    assert fmt.val(None) == "—"
    assert fmt.val(1.23456, nd=3) == "1.235"


def test_pct_signed_and_none():
    assert fmt.pct(0.0123) == "+1.23%"
    assert fmt.pct(-0.0123) == "-1.23%"
    assert fmt.pct(None) == "—"


def test_rupee_thousands_and_none():
    assert fmt.rupee(12345) == "₹12,345"
    assert fmt.rupee(-1366) == "₹-1,366"
    assert fmt.rupee(None) == "—"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
