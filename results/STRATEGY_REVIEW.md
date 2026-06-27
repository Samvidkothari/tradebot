# Tradebot — Strategy Review

**As of 2026-06-27.** Everything below is **paper / simulated** — no real orders
were ever placed. Strategies are judged against the standing benchmark: **NIFTY 50
buy-and-hold ≈ +7–8% CAGR at ~half the effort**. Equity strategies use a
2024-01-01 out-of-sample (OOS) split and after-costs returns. Backtest figures are
on data through 2026-06-25.

## Summary

| # | Strategy | Type | Verdict | Why |
|---|---|---|---|---|
| 1 | SMA 20/50 crossover | Equity trend-following | ❌ RETIRED | Underperformed NIFTY (+3.3% vs +8.0% CAGR) |
| 2 | 12-1 Momentum (top-15) | Equity cross-sectional | ❌ FAILED | Beat CAGR but −27.2% drawdown vs NIFTY −15.8% |
| 3 | **Low-volatility anomaly** | Equity cross-sectional | ✅ PASSED | Beats NIFTY in full + OOS *at lower drawdown* |
| 4 | Intraday ORB | Intraday breakout | ⚰️ RETIRED | Edge didn't survive costs (net −₹15,893) |
| 5 | Intraday VWAP | Intraday mean-reversion | ⚰️ RETIRED | Edge didn't survive costs (net −₹5,616) |
| 6 | Options short strangle | Options (VRP) | 🟡 INCONCLUSIVE | No vol event yet; unlimited tail |
| 7 | Options iron condor | Options (defined-risk) | 🟡 INCONCLUSIVE | No vol event yet; capped tail |

## Detail

### 1. SMA 20/50 crossover — RETIRED (Phase 2)
Classic trend-following. Underperformed buy-and-hold (+3.3% vs +8.0% CAGR).
Trend-following on this universe was retired.

### 2. Cross-sectional momentum (12-1, top-15 equal-weight, monthly) — FAILED (Phase 2B, attempt 1)
- Full CAGR **+14.7%** vs NIFTY +8.8%; OOS **+6.6%** vs +4.2%
- Max drawdown **−27.2%** vs NIFTY −15.8% (~1.7×)
- Fails the pre-committed drawdown criterion: it beats the index by *amplifying*
  risk. Confirmed unchanged on fresh data.

### 3. Low-volatility anomaly (60-day realized vol, hold 15 lowest, monthly) — PASSED (Phase 2B) — the one winner
- Full CAGR **+11.1%** vs NIFTY +6.9%; Total +64.9%
- Out-of-sample (2024+) **+7.6%** vs +4.2%
- Max drawdown **−15.9%** vs NIFTY −17.2% (*beats* the index on risk)
- 431 position-changes
- **Passes all 3 pre-committed criteria.** Now in Phase 3 live paper trading.
- Caveat: survivorship bias (today's NIFTY 50 membership applied to the past) —
  acknowledged, not corrected.

### 4 & 5. Intraday ORB + VWAP — RETIRED 2026-06-26 (monitoring sandbox)
- Combined gross edge **+₹11,405**; transaction costs **−₹32,915** over ~10 days
  → net **−₹21,509**.
- Finding: a thin intraday edge does not survive realistic costs (~0.08% of
  turnover per round trip × 5 trades/day). ORB whipsawed by stops; VWAP run over
  on trend days.
- Per their specs these were never pass/fail auditions — this is a "useful
  finding," not a tuned-away failure. `intraday.db` kept as evidence.

### 6 & 7. Options short strangle + iron condor — INCONCLUSIVE (forward paper)
Both harvest the volatility risk premium (selling overpriced options),
model-priced, running head-to-head on the same days:

| | Strangle (naked) | Iron condor (defined-risk) |
|---|---|---|
| Credit collected | ₹12,209 | ₹6,766 |
| Worst case | **unlimited** | **capped ₹30,734** |

Verdict is deliberately withheld until a real ≥4% NIFTY day tests them. No such
event has occurred, so neither is proven. The condor gives up premium to cap the
tail; the open question is whether that insurance pays off through a real shock.

## Current P&L snapshot (all paper)
- **Active books: +₹3,989** — low-vol +₹7,223, strangle −₹1,368, condor −₹1,866
- **Retired intraday: −₹21,510** — the paper-only tuition for the cost lesson

## Methodology & discipline (held throughout)
- Every strategy **pre-registered** (thesis + spec committed) **before** any test.
- **One parameter set** per thesis; if it fails, the thesis fails — **no
  result-driven re-tuning**.
- Judged **after costs** with a 2024-01-01 **out-of-sample** split, vs NIFTY 50
  buy-and-hold.
- Hard budget of two Phase 2B strategy classes; search closed once low-vol passed.
- **Zero order-placement code — every "trade" is a simulated database row.**

## Takeaways for review
1. **One genuine winner:** low-vol — and it wins the *right* way, beating the
   index while *reducing* drawdown.
2. **The recurring lesson:** friction kills thin edges — transaction costs sank
   intraday; bid-ask spread is the open question for options.
3. **Nothing is live.** Real-money trading remains a firm no until a strategy
   passes its pre-committed live test, and even then only semi-automatically
   (bot proposes, human approves) — never fully autonomous.
