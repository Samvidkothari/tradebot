"""
config.py — shared configuration constants (single source of truth).

PAPER_CAPITAL is the starting simulated balance for every paper book (low-vol
equity, options strangle, iron condor, intraday). Each book is its own
independent ₹PAPER_CAPITAL account. This value was previously duplicated as
CAPITAL / STARTING_CAPITAL across ~9 modules (dashboard.py even carried a
"must match ..." comment); it now lives here once.

PAPER ONLY — this is simulated money. No real capital is ever at risk; nothing
in this project places an order.
"""

PAPER_CAPITAL = 1_000_000   # ₹10,00,000 simulated, per book
