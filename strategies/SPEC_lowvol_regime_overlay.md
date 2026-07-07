# SPEC — Low-Vol Regime Overlay (defensive sizing, live paper book only)

**Pre-registered 2026-07-04, BEFORE activation.** Parameters below are locked;
if the overlay disappoints, the overlay fails — no re-tuning to results.

## What it is

A defensive **sizing overlay** on the live low-volatility paper book
(`paper_trader.py`). On a **rebalance day only**, the market-regime classifier
(`regime.py`, unchanged) is read on NIFTY closes. If the market is in
**extreme stress**, every target position is scaled down and the freed capital
stays in cash until the next monthly rebalance.

## What it is NOT

- It does **not** touch the pre-registered low-vol strategy: same signal
  (60-day realized vol), same universe, same TOP_N=15, same monthly cadence,
  same cost model. Selection is unchanged — only *size* changes.
- It does **not** modify `backtest_lowvol.py` or any committed verdict. The
  PASSED backtest remains byte-identical. This overlay is forward, paper-only.
- It never *increases* exposure. Factor is 1.0 or 0.5, nothing else.

## Locked parameters

| Parameter | Value | Rule |
|---|---|---|
| `STRESS_VOL_PCTL` | **0.85** | NIFTY 20d realized vol ≥ 85th percentile of its trailing year |
| Stress trend | **bear** | `regime.classify` trend axis = bear (price < 200-day MA, MA falling) |
| `STRESS_EXPOSURE` | **0.50** | Both conditions true → target each name at 0.5 × (equity / 15) |
| Normal exposure | **1.00** | Otherwise — including any data failure (fail-safe: overlay off) |

Stress requires **both** axes (bear AND extreme vol): high vol alone in a bull
market is not the drawdown regime low-vol needs protecting from, and a mild
bear with normal vol is what the strategy already handles (its backtest DD was
−15.9% vs index −17.2%).

## Audit trail

Every rebalance logs the overlay decision (factor, regime tags, measures,
reason) to `portfolio.db` meta (`last_regime_overlay`) and prints it in the
run log. The dashboard shows the factor when it is active.

## Rationale (thesis)

Low-vol's edge is *relative* resilience; it still draws down in absolute terms
in a bear+vol-spike regime. Halving exposure in that (historically rare) state
trades some upside for a smaller left tail — the same defensive intent as the
strategy itself, applied at the portfolio level. Monthly rebalancing means the
overlay reacts with up to a month's lag by design; it is a brake, not a timer.

*Paper only. No real orders. Evaluation: compare the live book's drawdown vs a
shadow no-overlay book over ≥1 stress episode before judging.*
