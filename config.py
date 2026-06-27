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

# Out-of-sample split shared by every equity backtest / research module.
SPLIT_DATE = "2024-01-01"   # Period B (out-of-sample) starts here

# ── Cost model (Zerodha equity delivery) ─────────────────────────────────────
# Moved here from backtest.py so the cost model is a shared config value rather
# than a constant exported by the retired SMA backtest. Values are UNCHANGED.
BROKERAGE_PER_SIDE = 0.000000   # ₹0 for delivery equity on Zerodha
STT_BUY            = 0.001000   # 0.10% of buy turnover
STT_SELL           = 0.001000   # 0.10% of sell turnover
EXCHANGE_CHARGE    = 0.0000345  # NSE: 0.00345% each side
SEBI_CHARGE        = 0.0000010  # 0.0001% each side
STAMP_DUTY         = 0.0001500  # 0.015% on buy only
GST_RATE           = 0.180000   # 18% on (brokerage + exchange charges)
SLIPPAGE_PER_SIDE  = 0.000500   # 0.05% per side — conservative for large-caps

_taxable_entry = BROKERAGE_PER_SIDE + EXCHANGE_CHARGE + SEBI_CHARGE
_taxable_exit  = BROKERAGE_PER_SIDE + EXCHANGE_CHARGE + SEBI_CHARGE
COST_ENTRY     = (SLIPPAGE_PER_SIDE + STT_BUY + STAMP_DUTY
                  + _taxable_entry + GST_RATE * _taxable_entry)
COST_EXIT      = (SLIPPAGE_PER_SIDE + STT_SELL
                  + _taxable_exit  + GST_RATE * _taxable_exit)
COST_ROUNDTRIP = COST_ENTRY + COST_EXIT
