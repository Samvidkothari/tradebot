"""
run_tests.py — one-command test runner for the whole project.

Discovers every test_*.py, runs each `test_*` function, and prints a single
green/red summary with a proper exit code (0 = all pass, 1 = any failure). No
third-party dependency (pytest is not installed); the existing test files are
plain functions, which this imports and calls directly.

Usage:
  python run_tests.py            # run everything
  python run_tests.py metrics    # run only test_metrics.py (substring match)
"""

import importlib
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).parent


def discover(filter_substr=None):
    files = sorted(BASE.glob("test_*.py"))
    if filter_substr:
        files = [f for f in files if filter_substr in f.stem]
    return files


def run_module(modname):
    mod = importlib.import_module(modname)
    fns = [getattr(mod, n) for n in sorted(dir(mod)) if n.startswith("test_")]
    passed, failed = 0, []
    for fn in fns:
        try:
            fn()
            passed += 1
        except Exception:
            failed.append(fn.__name__)
            print(f"    FAIL  {modname}.{fn.__name__}")
            traceback.print_exc()
    return passed, failed


def main(argv):
    filt = argv[1] if len(argv) > 1 else None
    files = discover(filt)
    if not files:
        print("No test files matched."); return 1

    total_pass, total_fail, mod_fail = 0, 0, 0
    print(f"Running {len(files)} test module(s)...\n")
    for f in files:
        p, fails = run_module(f.stem)
        total_pass += p
        total_fail += len(fails)
        mod_fail += 1 if fails else 0
        status = "ok" if not fails else f"{len(fails)} FAILED"
        print(f"  {f.stem:<24} {p:>2} passed   {status}")

    print(f"\n{'='*48}")
    if total_fail:
        print(f"  {total_pass} passed, {total_fail} FAILED across {mod_fail} module(s)")
    else:
        print(f"  ALL GREEN — {total_pass} tests passed in {len(files)} modules")
    print(f"{'='*48}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
