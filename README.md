# tradebot

A **research and paper-trading** platform for Indian equities and derivatives (NSE).
It selects, sizes, and reviews trades through a governed pipeline — but it is
**research/paper only and places no live orders**. Broker access is strictly
read-only (holdings/auth).

> ⚠️ **Not investment advice and not a live trading system.** Everything here is
> for backtesting, simulation, and paper trading. Nothing in this repo submits,
> modifies, or cancels an order.

## What it does

The core idea is *offense governed by defense* (see [`PLAYBOOK.md`](PLAYBOOK.md)):

| Layer | Role | Where |
|---|---|---|
| Selection | Pick stocks "in play" — relative-volume surge, thrust to a fresh high | `episodic_pivot.py` |
| Regime gate | Refuse entries in a "nothing works" tape (chop / bear + high-vol) | `regime.py`, `regime_overlay.py` |
| Sizing | Scale each entry by a graded fractional-Kelly exposure factor | `varma_riskstate.py` |
| Exit | Sell into strength: book half into the first spike, then trail | `trailing_exit.py` |

Around that sit backtests (momentum, low-vol, price-action, futures trend,
options/condor), a research assistant, attribution/tearsheet reporting, a risk
governor, and a local Flask dashboard.

## Stack

- **Python 3** — `pandas`, `numpy` for research/backtests
- **Flask** — local read-only dashboard
- **kiteconnect** (Zerodha Kite) — read-only holdings/auth only
- **yfinance** — free daily/intraday price data
- **python-dotenv** — loads secrets from `.env`

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

Then create a `.env` in the project root with your own credentials:

The `.env` is gitignored — never commit it:

```
KITE_API_KEY=your_key
KITE_API_SECRET=your_secret
DASHBOARD_PASSWORD=your_password
TV_WEBHOOK_SECRET=your_webhook_secret
```

## Common tasks

```bash
make test      # unit + route test suite (pytest)
make smoke     # hit every dashboard route in-process, assert 200
make check     # pre-deploy gate: test + smoke
make run       # start the local dashboard
```

## Layout

- `backtest_*.py` — strategy backtests (momentum, low-vol, price-action, futures trend, episodic pivot)
- `strategies/` — strategy specs and theses
- `risk_governor.py`, `risk_engine.py`, `controls.py` — risk limits and monitoring (flags only, no orders)
- `paper_trader.py` — paper-trading loop
- `dashboard.py`, `views_*.py`, `templates/` — local Flask dashboard
- `research_assistant.py`, `research_pipeline.py` — research tooling
- Design/doctrine docs: `PLAYBOOK.md`, `VARMA_DOCTRINE.md`, `FUND_BLUEPRINT_*.md`, `ARCHITECTURE_REVIEW.md`

## Security

Secrets (`.env`, `token.json`), databases (`*.db`), the virtualenv, logs, and
caches are gitignored and never committed. All API keys are read from
environment variables at runtime — none are hardcoded.

## License

No license yet — all rights reserved by the author unless a `LICENSE` file is added.
