# SPEC — Price-Action Swing (market structure + supply/demand + R:R)

**Pre-registered:** 2026-07-01, BEFORE any price-action backtest was run.
No parameter in this file may change after seeing results (Phase 2B Rule 3).
One parameter set per thesis. If it fails the pass criteria, the thesis failed.

Origin: a popular 3-step "price action" method (market structure → supply/demand
zones → risk-reward filter). This spec turns that *discretionary* method into
*mechanical* rules so it can be judged after costs instead of eyeballed on
hand-picked charts.

---

## 1. Thesis (why should this earn excess return?)

Trend-following retracement entries claim to exploit two things: (a) trends
persist (institutional flow, underreaction), so trading *with* structure beats
fading it; and (b) "supply/demand zones" are prior consolidations where resting
orders refill, giving a low-risk entry with a nearby stop and a far target — a
favourable risk-reward. The edge, *if any*, is behavioural + flow-based.

The honest doubt: the zone step is where discretionary versions cheat — a base
is only obvious *after* the move. Forced to define it mechanically and pay real
costs, most such "edges" collapse. This spec exists to find out which it is.

## 2. Universe & data
NIFTY-50 cash names (data/*.csv, daily OHLC), survivorship-biased (current
members), 2021-06 → present. Daily bars only — no intraday.

## 3. Mechanical rules (all objective, no look-ahead)

**Step 1 — Market structure.** Swing pivots are 5-bar fractals (`L=2`): a swing
high/low is confirmed only `L` bars *after* it prints. Trend flips **up** when
close breaks the last *confirmed* swing high (break-of-structure), **down** when
it breaks the last confirmed swing low.

**Step 2 — Supply/demand zone.** A zone forms when a **base** of `BASE_MIN=3`
bars, each with range ≤ `BASE_ATR=0.8 × ATR(14)`, is immediately followed by an
**impulse** bar moving > `IMP_ATR=1.5 × ATR` in the trend direction. Demand zone
(up-impulse) = the base's low..high; supply zone (down-impulse) = base low..high.
A zone expires after `ZONE_TTL=40` bars or once used.

**Step 3 — Entry / stop / target / R:R.** When flat and price retraces into a
fresh zone in the trend direction: enter at the zone edge, stop `SL_BUF=0.5 ×
ATR` beyond the far edge, target = last confirmed swing (high for longs, low for
shorts). Take the trade **only if** `(target−entry)/(entry−stop) ≥ RR_MIN=2.5`.
Long from demand in uptrends, short from supply in downtrends. Force-exit after
`MAX_HOLD=40` bars. On each later bar the stop is checked before the target
(conservative). Fixed **1% equity risk per trade**.

## 4. Costs
Every round trip pays `config.COST_ROUNDTRIP` (≈0.32%: slippage + STT + stamp +
exchange/SEBI + GST). No free fills.

## 5. Pass criteria (judged after costs)
The thesis PASSES only if ALL hold:
1. Positive net expectancy per trade (mean R after costs > 0).
2. Profit factor ≥ 1.3.
3. Holds out-of-sample: mean R after costs > 0 on trades closing on/after
   SPLIT_DATE (2024-01-01).
4. Sample ≥ 100 trades (otherwise: INCONCLUSIVE, not PASS).

Anything else = FAIL. Judged vs the null that this is discretionary hindsight
with no durable, cost-surviving edge.
