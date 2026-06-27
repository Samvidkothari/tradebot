"""
fmt.py — shared display formatters for the research runners.

Consolidates the tiny per-runner helpers (`_f`/`_pct`/`_r`) that printed numbers,
percentages and rupee amounts. None-safe by default (returns an em-dash). Used by
the research/report runners only — the pre-registered/live sims keep their own
`rupees()` helper to avoid touching protected code for a one-liner.

Pure formatting; no I/O, no orders.
"""

from __future__ import annotations


def val(x, pct: bool = False, nd: int = 2) -> str:
    """Number, or signed percentage when pct=True; em-dash for None."""
    if x is None:
        return "—"
    return f"{x*100:+.{nd}f}%" if pct else f"{x:.{nd}f}"


def pct(x, nd: int = 2) -> str:
    """Signed percentage (e.g. +1.23%); em-dash for None."""
    return "—" if x is None else f"{x*100:+.{nd}f}%"


def rupee(x, nd: int = 0) -> str:
    """Rupee amount (e.g. ₹12,345); em-dash for None."""
    return "—" if x is None else f"₹{x:,.{nd}f}"
