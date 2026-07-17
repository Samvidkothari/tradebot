"""
vwap_bot.py — 15-minute intraday VWAP mean-reversion bot (NSE equity, MOCK I/O).

STATUS IN THIS REPO: research candidate, NOT promoted. SPEC_vwap.md was
CONCLUDED 2026-06-26 — the last VWAP intraday edge did not survive costs.
This implementation exists so the *mean-reversion* variant can be evaluated
the honest way: run it (paper), feed its per-trade gross returns to
cost_gate.evaluate(), and let the pre-committed gate rule. Default is FAIL.

Strategy (evaluated ONLY on closed 15m candles — no intra-bar peeking):
  VWAP     session-anchored: cum(TP·V) / cum(V), TP = (H+L+C)/3, RESET at
           every session open (09:15 IST).
  Bands    volume-weighted std-dev around VWAP: σ² = cum(TP²·V)/cum(V) − VWAP².
           Upper = VWAP + K·σ, Lower = VWAP − K·σ (K = 2.0).
  LONG     prev candle closed below the Lower Band AND current candle closes
           UP vs prev close (minor upward reversal) while still under VWAP.
  SHORT    mirror image above the Upper Band.
  EXIT     close crosses back over VWAP (target), else the risk stack below.

Ruthless risk stack (checked in this order, every candle):
  1. Hard stop      0.50% adverse move from entry. Protection is BROKER-SIDE:
                    a resting SL-M stop order (place_stop_order) is submitted
                    the moment the entry fills, so live, the EXCHANGE executes
                    it mid-bar — the 15m loop never leaves you unprotected
                    waiting for a candle to close. The per-candle check here
                    is the sim/paper reconciliation of that order: gap-aware,
                    filling at the worse of stop price / candle open. Do NOT
                    replace this with 1-minute polling on delayed data — a
                    polled stop on 15-min-lagged quotes is an illusion of
                    safety; the resting order is the real thing.
  2. Circuit breaker  day's REALIZED PnL ≤ −2.0% of the day-start balance →
                    flatten, halt all trading until the next session.
  3. Max hold       2 hours (8 candles) → force-close at market.
  4. EOD flatten    never hold past the 15:15 candle close (MIS square-off).

Execution/infra: asyncio loop aligned to 15m boundaries; ALL network calls go
through retry with exponential backoff + jitter behind a token-interval rate
limiter. Data/orders are MOCK adapters (`fetch_latest_candles`,
`place_market_order`, `place_limit_order`) — swap in Kite/ccxt/alpaca there.

Friction: taker fee per side charged on entry AND exit notional. Default is
this repo's conservative NSE intraday per-side cost (≈ cost_gate round trip /
2), not the optimistic exchange-brochure number.

MOCK / PAPER ONLY — place_market_order prints and records; nothing reaches a
real exchange until YOU replace the adapters, and nothing should until the
cost gate passes.

Run a simulated 2-day demo session:   python vwap_bot.py --demo
Run the live-shaped loop (mock feed): python vwap_bot.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from typing import Callable

import notify_telegram

# ── Configuration ─────────────────────────────────────────────────────────────
SYMBOL            = "RELIANCE"        # NSE symbol (mock feed synthesizes it)
CANDLE_MINUTES    = 15
BAND_K            = 2.0              # std-dev multiplier for the bands
MIN_BARS_FOR_SIG  = 4                # warm-up bars before bands are trusted

HARD_STOP_PCT     = 0.005            # 0.5% adverse move → stop out
DAILY_LOSS_PCT    = 0.020            # −2% realized on the day → circuit breaker
MAX_HOLD_BARS     = 8                # 8 × 15m = 2 hours
SESSION_OPEN      = dtime(9, 15)     # NSE
SESSION_CLOSE     = dtime(15, 30)
FLATTEN_AFTER     = dtime(15, 15)    # last candle close we'll hold through

STARTING_BALANCE  = 1_000_000.0      # ₹, paper
POSITION_FRACTION = 0.95             # of balance deployed per trade (1 position max)

# Per-side taker fee on notional. 0.0012 ≈ this repo's conservative NSE
# intraday round trip (cost_gate.round_trip_cost(0.01) ≈ 0.245%) split per
# side: slippage + brokerage + STT + exchange/GST. Template's crypto-style
# 0.1% would UNDERSTATE NSE equity costs.
FEE_PER_SIDE      = 0.0012

# Network discipline
MAX_RETRIES       = 5
BACKOFF_BASE_S    = 1.5
MIN_CALL_GAP_S    = 0.35             # global rate-limit floor between API calls

# Varma risk-state sizing (varma_riskstate.py / SPEC_varma_riskstate.md):
# per-trade notional is scaled by the graded exposure factor in [0.40, 1.0]
# computed ONCE per session from daily NIFTY closes ("classify, don't
# predict"; brake, not timer). Fail-safe on any data problem is the sizer's
# own conservative NEUTRAL_FACTOR (0.75), never full size.
VARMA_SIZING      = True
VARMA_FAILSAFE    = 0.75             # used only if the module itself won't load
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("vwap_bot")


# ══ Data structures ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Candle:
    ts: datetime                     # candle CLOSE timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0


class SessionVWAP:
    """Incremental session-anchored VWAP + volume-weighted std-dev bands.

    Three scalars — cum(TP·V), cum(TP²·V), cum(V) — are all the state needed:
    each closed candle folds in with O(1) work and O(1) memory, no arrays, no
    recomputation over the day's history. Bands come from the identity
    Var = E[TP²] − E[TP]² under volume weighting. reset() runs at every new
    session date, satisfying the hard requirement that cumulative arrays
    restart each trading day.
    """

    def __init__(self) -> None:
        self.session: date | None = None
        self.cum_pv = self.cum_p2v = self.cum_v = 0.0
        self.n_bars = 0

    def reset(self, session: date) -> None:
        self.session = session
        self.cum_pv = self.cum_p2v = self.cum_v = 0.0
        self.n_bars = 0
        log.info("session %s — VWAP state reset", session)

    def update(self, c: Candle) -> None:
        if c.ts.date() != self.session:
            self.reset(c.ts.date())
        tp, v = c.typical, max(c.volume, 0.0)
        self.cum_pv += tp * v
        self.cum_p2v += tp * tp * v
        self.cum_v += v
        self.n_bars += 1

    @property
    def vwap(self) -> float | None:
        return self.cum_pv / self.cum_v if self.cum_v > 0 else None

    def bands(self, k: float = BAND_K) -> tuple[float, float, float] | None:
        """(lower, vwap, upper) or None during warm-up."""
        if self.cum_v <= 0 or self.n_bars < MIN_BARS_FOR_SIG:
            return None
        vwap = self.cum_pv / self.cum_v
        var = max(self.cum_p2v / self.cum_v - vwap * vwap, 0.0)
        sd = math.sqrt(var)
        if sd <= 0:
            return None
        return vwap - k * sd, vwap, vwap + k * sd


@dataclass
class Position:
    side: str                        # "LONG" | "SHORT"
    qty: int
    entry_price: float
    entry_ts: datetime
    entry_fee: float
    bars_held: int = 0
    stop_order_id: str | None = None # resting broker-side SL-M protecting this

    @property
    def stop_price(self) -> float:
        m = (1 - HARD_STOP_PCT) if self.side == "LONG" else (1 + HARD_STOP_PCT)
        return self.entry_price * m


# ══ Mock adapters — REPLACE THESE with your real API (Kite / ccxt / alpaca) ════

class MockFeed:
    """Synthetic 15m NSE candles: a noisy mean-reverting walk with volume
    spikes, so the demo produces band touches, stop-outs and VWAP reversion.
    Replace fetch_latest_candles() with your data API; keep the signature."""

    def __init__(self, seed: int = 7, anchor: float = 1300.0) -> None:
        self.rng = random.Random(seed)
        self.px = anchor
        self.anchor = anchor

    def next_candle(self, ts: datetime) -> Candle:
        drift = 0.15 * (self.anchor - self.px) / self.anchor      # mean reversion
        shock = self.rng.gauss(0, 0.0028)                         # ~0.28%/bar noise
        o = self.px
        c = o * (1 + drift * 0.01 + shock)
        hi = max(o, c) * (1 + abs(self.rng.gauss(0, 0.0008)))
        lo = min(o, c) * (1 - abs(self.rng.gauss(0, 0.0008)))
        v = self.rng.uniform(0.8, 1.4) * 100_000
        if self.rng.random() < 0.08:                              # volume spike
            v *= 3
        self.px = c
        return Candle(ts, round(o, 2), round(hi, 2), round(lo, 2), round(c, 2), v)


async def fetch_latest_candles(feed: MockFeed, ts: datetime,
                               symbol: str = SYMBOL) -> list[Candle]:
    """MOCK. Real impl: return the most recent CLOSED 15m candle(s) for
    `symbol` from your API. May raise on network errors — the caller retries."""
    await asyncio.sleep(0)                                        # yield point
    return [feed.next_candle(ts)]


async def place_market_order(symbol: str, side: str, qty: int,
                             ref_price: float) -> dict:
    """MOCK. Real impl: submit a market order, return the actual fill.
    The mock fills at ref_price (the decision candle's close / stop level)."""
    await asyncio.sleep(0)
    fill = {"symbol": symbol, "side": side, "qty": qty, "price": ref_price}
    log.info("ORDER  %-4s %-10s qty=%-5d @ %.2f  [MOCK — no real order]",
             side, symbol, qty, ref_price)
    return fill


async def place_limit_order(symbol: str, side: str, qty: int,
                            limit_price: float) -> dict:
    """MOCK. Provided for the plug-in interface; the strategy itself uses
    market orders on candle close for determinism."""
    await asyncio.sleep(0)
    log.info("ORDER  %-4s %-10s qty=%-5d LIMIT %.2f  [MOCK — no real order]",
             side, symbol, qty, limit_price)
    return {"symbol": symbol, "side": side, "qty": qty, "price": limit_price}


_stop_seq = 0


async def place_stop_order(symbol: str, side: str, qty: int,
                           trigger_price: float) -> dict:
    """MOCK. Real impl: submit a STOP-MARKET (Kite: SL-M) order resting AT THE
    EXCHANGE so the stop executes mid-bar without this process being awake.
    Returns an order id for later cancellation."""
    global _stop_seq
    await asyncio.sleep(0)
    _stop_seq += 1
    oid = f"STOP-{_stop_seq}"
    log.info("ORDER  %-4s %-10s qty=%-5d SL-M trig %.2f  id=%s  "
             "[MOCK — no real order]", side, symbol, qty, trigger_price, oid)
    return {"order_id": oid, "symbol": symbol, "side": side,
            "qty": qty, "trigger": trigger_price}


async def cancel_order(order_id: str) -> None:
    """MOCK. Real impl: cancel the resting order (e.g. the protective stop,
    once the position exits by target/time/EOD instead)."""
    await asyncio.sleep(0)
    log.info("ORDER  CANCEL %s  [MOCK — no real order]", order_id)


# ══ Network discipline: rate limiter + retry with exponential backoff ══════════

class RateLimiter:
    """Global floor between outbound calls — the IP-ban insurance."""

    def __init__(self, min_gap_s: float = MIN_CALL_GAP_S) -> None:
        self.min_gap = min_gap_s
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            gap = self.min_gap - (now - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = asyncio.get_event_loop().time()


_limiter = RateLimiter()


async def with_retry(coro_fn: Callable, *args, what: str = "call", **kw):
    """Run an async API call through the rate limiter with exponential
    backoff + jitter. Rate-limit-looking errors get an extra cool-off.
    Raises only after MAX_RETRIES — callers decide whether that's fatal."""
    for attempt in range(MAX_RETRIES):
        await _limiter.wait()
        try:
            return await coro_fn(*args, **kw)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                log.error("%s failed after %d attempts: %s", what, MAX_RETRIES, e)
                raise
            sleep = BACKOFF_BASE_S * (2 ** attempt) + random.uniform(0, 0.5)
            if any(t in str(e).lower() for t in ("429", "rate", "too many")):
                sleep += 20
                log.warning("%s rate-limited — cooling off %.0fs", what, sleep)
            else:
                log.warning("%s error (%s) — retry %d/%d in %.1fs",
                            what, e, attempt + 1, MAX_RETRIES, sleep)
            await asyncio.sleep(sleep)


# ══ The bot ════════════════════════════════════════════════════════════════════

class VwapMeanReversionBot:
    def __init__(self, symbol: str = SYMBOL,
                 balance: float = STARTING_BALANCE,
                 nifty_loader: Callable | None = None) -> None:
        self.symbol = symbol
        self.balance = balance
        self.nifty_loader = nifty_loader           # None -> load_cached_nifty
        self.varma_factor = 1.0
        self.varma_reason = "not yet read"
        # Push alerts: fire-and-forget, never blocks a candle, never raises
        # (notify_telegram contract). No-op unless .env has the two keys.
        self.notify = notify_telegram.get_notifier()
        self.vwap = SessionVWAP()
        self.pos: Position | None = None
        self.prev: Candle | None = None
        self.prev_bands: tuple[float, float, float] | None = None
        self.day_start_balance = balance
        self.day_realized = 0.0
        self.halted_for: date | None = None
        self.trades: list[dict] = []            # closed-trade log (gross + net)
        self.fees_paid = 0.0

    # ── session bookkeeping ───────────────────────────────────────────────────
    def _roll_session(self, d: date) -> None:
        if self.vwap.session == d:
            return
        self.day_start_balance = self.balance
        self.day_realized = 0.0
        if self.halted_for and self.halted_for != d:
            self.halted_for = None
            log.info("circuit breaker cleared — new session %s", d)
        self.prev = None
        self.prev_bands = None
        # Varma risk state: ONE read per session, from daily closes up to
        # yesterday (no look-ahead). Applies to every entry sized today.
        loader = self.nifty_loader or load_cached_nifty
        closes = loader()
        if closes is not None:
            try:
                closes = closes[closes.index < str(d)]   # strictly prior days
            except Exception:
                pass
        self.varma_factor, self.varma_reason = varma_read(closes)
        log.info("VARMA  session %s — exposure %.0f%% | %s",
                 d, self.varma_factor * 100, self.varma_reason)
        # SessionVWAP.reset happens inside update()

    @property
    def halted(self) -> bool:
        return self.halted_for is not None

    # ── accounting ────────────────────────────────────────────────────────────
    def _fee(self, qty: int, price: float) -> float:
        return qty * price * FEE_PER_SIDE

    async def _open(self, side: str, c: Candle) -> None:
        # Varma sizing: position sizing IS risk management — the graded factor
        # scales notional down as the risk state worsens (never above 1.0).
        qty = int((self.balance * POSITION_FRACTION * self.varma_factor)
                  // c.close)
        if qty < 1:
            log.warning("signal %s skipped — balance too small for 1 share", side)
            return
        fill = await with_retry(place_market_order, self.symbol,
                                "BUY" if side == "LONG" else "SELL",
                                qty, c.close, what="entry order")
        fee = self._fee(qty, fill["price"])
        self.fees_paid += fee
        self.pos = Position(side, qty, fill["price"], c.ts, fee)
        # Protection goes live IMMEDIATELY: resting broker-side stop, so a
        # mid-bar crash is handled by the exchange, not the next candle close.
        try:
            stop = await with_retry(
                place_stop_order, self.symbol,
                "SELL" if side == "LONG" else "BUY",
                qty, round(self.pos.stop_price, 2), what="stop order")
            self.pos.stop_order_id = stop.get("order_id")
        except Exception:
            log.error("protective stop could NOT be placed — flattening entry")
            await self._close(c, c.close, "STOP PLACEMENT FAILED — flatten")
            return
        log.info("ENTER  %-5s qty=%d @ %.2f  stop=%.2f (resting %s)  fee=%.2f",
                 side, qty, fill["price"], self.pos.stop_price,
                 self.pos.stop_order_id, fee)
        self.notify.send(
            f"🟢 ENTER {side} — {self.symbol}\n"
            f"qty {qty} @ ₹{fill['price']:.2f}  (stop ₹{self.pos.stop_price:.2f}, "
            f"varma {self.varma_factor:.0%})  [PAPER]")

    async def _close(self, c: Candle, exit_price: float, reason: str) -> None:
        p = self.pos
        assert p is not None
        # Retire the resting protective stop first. On a stop-reason exit the
        # broker already consumed it; cancelling a filled order is a no-op in
        # real APIs, so the mock mirrors that unconditionally-safe shape.
        if p.stop_order_id and "HARD STOP" not in reason:
            try:
                await with_retry(cancel_order, p.stop_order_id,
                                 what="stop cancel")
            except Exception:
                log.error("could not cancel resting stop %s — real adapter "
                          "must reconcile before next entry", p.stop_order_id)
        fill = await with_retry(place_market_order, self.symbol,
                                "SELL" if p.side == "LONG" else "BUY",
                                p.qty, exit_price, what="exit order")
        exit_fee = self._fee(p.qty, fill["price"])
        self.fees_paid += exit_fee
        sign = 1 if p.side == "LONG" else -1
        gross = sign * (fill["price"] - p.entry_price) * p.qty
        net = gross - p.entry_fee - exit_fee
        self.balance += net
        self.day_realized += net
        self.trades.append({
            "session": c.ts.date().isoformat(), "side": p.side, "qty": p.qty,
            "entry": p.entry_price, "exit": fill["price"], "bars": p.bars_held,
            "gross": round(gross, 2), "fees": round(p.entry_fee + exit_fee, 2),
            "net": round(net, 2), "reason": reason,
            "gross_ret": sign * (fill["price"] / p.entry_price - 1),
            "varma": self.varma_factor,
        })
        log.info("EXIT   %-5s qty=%d @ %.2f  (%s)  gross=%+.2f  net=%+.2f  "
                 "day-realized=%+.2f", p.side, p.qty, fill["price"], reason,
                 gross, net, self.day_realized)
        icon = ("🛑" if "HARD STOP" in reason else
                "⛔" if "CIRCUIT" in reason else
                "🎯" if "TARGET" in reason else "🌇")
        self.notify.send(
            f"{icon} EXIT {p.side} — {self.symbol}\n"
            f"qty {p.qty} @ ₹{fill['price']:.2f}  ({reason})\n"
            f"net ₹{net:+,.2f} | day ₹{self.day_realized:+,.2f}  [PAPER]")
        self.pos = None

    # ── risk stack (order matters; returns True if position was closed) ───────
    async def _risk_checks(self, c: Candle) -> None:
        if self.pos:
            self.pos.bars_held += 1
            p = self.pos

            # 1. Hard stop — this is the sim/paper RECONCILIATION of the
            #    broker-side SL-M placed at entry: live, the exchange filled
            #    it mid-bar the moment price touched the trigger. Gap-aware:
            #    fill at the stop, or at the open if the bar gapped past it.
            stop = p.stop_price
            hit = (c.low <= stop) if p.side == "LONG" else (c.high >= stop)
            if hit:
                px = (min(stop, c.open) if p.side == "LONG"
                      else max(stop, c.open))
                await self._close(c, round(px, 2), "HARD STOP 0.5%")

        # 2. Circuit breaker — realized PnL only, vs day-start balance.
        if (not self.halted
                and self.day_realized <= -DAILY_LOSS_PCT * self.day_start_balance):
            self.halted_for = c.ts.date()
            log.warning("⛔ CIRCUIT BREAKER: day realized %+.2f ≤ −%.1f%% of %.0f "
                        "— trading HALTED until next session",
                        self.day_realized, DAILY_LOSS_PCT * 100,
                        self.day_start_balance)
            self.notify.send(
                f"⛔ CIRCUIT BREAKER — {self.symbol}\n"
                f"day realized ₹{self.day_realized:+,.2f} breached "
                f"−{DAILY_LOSS_PCT:.0%} of ₹{self.day_start_balance:,.0f}.\n"
                f"All trading HALTED until next session.  [PAPER]")
            if self.pos:
                await self._close(c, c.close, "CIRCUIT BREAKER flatten")

        if self.pos:
            # 3. Max holding time.
            if self.pos.bars_held >= MAX_HOLD_BARS:
                await self._close(c, c.close, f"MAX HOLD {MAX_HOLD_BARS} bars")
            # 4. EOD flatten (MIS square-off).
            elif c.ts.time() >= FLATTEN_AFTER:
                await self._close(c, c.close, "EOD FLATTEN")

    # ── signal logic (closed candles only) ────────────────────────────────────
    async def _signals(self, c: Candle, bands: tuple[float, float, float]) -> None:
        lower, vwap, upper = bands

        # Exits first: price crossed back over the session VWAP.
        if self.pos:
            if ((self.pos.side == "LONG" and c.close >= vwap) or
                    (self.pos.side == "SHORT" and c.close <= vwap)):
                await self._close(c, c.close, "TARGET: VWAP cross")
            return                                     # never flip on same bar

        if self.halted or c.ts.time() >= FLATTEN_AFTER:
            return                                     # no fresh risk late/halted

        if self.prev is None or self.prev_bands is None:
            return
        prev_lower, _, prev_upper = self.prev_bands

        # LONG: prev bar closed below ITS lower band; this bar turns up but
        # is still below VWAP (we're buying the stretch, not the recovery).
        if self.prev.close < prev_lower and c.close > self.prev.close \
                and c.close < vwap:
            log.info("SIGNAL LONG  — prev %.2f < band %.2f, reversal to %.2f "
                     "(VWAP %.2f)", self.prev.close, prev_lower, c.close, vwap)
            await self._open("LONG", c)
        # SHORT: mirror.
        elif self.prev.close > prev_upper and c.close < self.prev.close \
                and c.close > vwap:
            log.info("SIGNAL SHORT — prev %.2f > band %.2f, reversal to %.2f "
                     "(VWAP %.2f)", self.prev.close, prev_upper, c.close, vwap)
            await self._open("SHORT", c)

    # ── per-candle pipeline ───────────────────────────────────────────────────
    async def on_candle(self, c: Candle) -> None:
        self._roll_session(c.ts.date())
        self.vwap.update(c)
        bands = self.vwap.bands()
        b = (f"L={bands[0]:.2f} V={bands[1]:.2f} U={bands[2]:.2f}"
             if bands else "warming up")
        log.info("CANDLE %s  O=%.2f H=%.2f L=%.2f C=%.2f V=%.0f  | %s%s",
                 c.ts.strftime("%m-%d %H:%M"), c.open, c.high, c.low, c.close,
                 c.volume, b, "  [HALTED]" if self.halted else "")

        await self._risk_checks(c)
        if bands:
            await self._signals(c, bands)

        self.prev = c
        self.prev_bands = bands

    # ── the live-shaped loop ──────────────────────────────────────────────────
    async def run(self, feed: MockFeed) -> None:
        """Wake precisely at each 15m boundary (+2s grace for the candle to
        finalize), fetch the closed candle through retry/backoff, process."""
        log.info("bot up — %s, %dm candles, fee %.3f%%/side  [MOCK adapters]",
                 self.symbol, CANDLE_MINUTES, FEE_PER_SIDE * 100)
        self.notify.send(
            f"🤖 vwap_bot UP — {self.symbol}, {CANDLE_MINUTES}m VWAP "
            f"mean-reversion\nbalance ₹{self.balance:,.0f}, stop "
            f"{HARD_STOP_PCT:.1%}, breaker {DAILY_LOSS_PCT:.0%}  [PAPER, mock adapters]")
        while True:
            now = datetime.now()
            nxt = _next_boundary(now)
            await asyncio.sleep(max((nxt - now).total_seconds() + 2.0, 0))
            if not _in_session(nxt.time()):
                continue
            try:
                candles = await with_retry(fetch_latest_candles, feed, nxt,
                                           what="candle fetch")
            except Exception:
                log.error("feed unavailable this bar — skipping (no stale trades)")
                continue
            for c in candles:
                await self.on_candle(c)

    # ── reporting ─────────────────────────────────────────────────────────────
    def report(self) -> None:
        print(f"\n{'─'*74}\nTRADES ({len(self.trades)}):")
        for t in self.trades:
            print(f"  {t['session']}  {t['side']:<5} qty={t['qty']:<5} "
                  f"{t['entry']:>8.2f} → {t['exit']:>8.2f}  bars={t['bars']} "
                  f"varma={t.get('varma', 1.0):.2f} "
                  f"gross={t['gross']:>+9.2f} fees={t['fees']:>7.2f} "
                  f"net={t['net']:>+9.2f}  {t['reason']}")
        gross = sum(t["gross"] for t in self.trades)
        net = sum(t["net"] for t in self.trades)
        print(f"\n  balance ₹{self.balance:,.2f}  (start ₹{STARTING_BALANCE:,.0f})")
        print(f"  gross {gross:+,.2f} | fees {self.fees_paid:,.2f} | net {net:+,.2f}")
        if self.trades:
            wins = sum(1 for t in self.trades if t["net"] > 0)
            print(f"  win rate {wins}/{len(self.trades)}  "
                  f"avg gross/trade {gross/len(self.trades)/STARTING_BALANCE:+.4%} "
                  f"of book")
        # Cost-gate verdict, if run inside the tradebot repo (optional import).
        try:
            import pandas as pd
            import cost_gate
            g = pd.Series([t["gross_ret"] for t in self.trades])
            risk = pd.Series([HARD_STOP_PCT] * len(g))
            res = cost_gate.evaluate(cost_gate.GateInputs(
                gross_ret=g, risk=risk, trades_per_year=len(g) * 125))
            print(f"\n{cost_gate.format_report(res, 'VWAP mean-reversion 15m')}")
        except Exception:
            pass
        print("─" * 74)


# ── Varma risk-state provider ─────────────────────────────────────────────────

def load_cached_nifty():
    """Default NIFTY daily-closes loader: the repo's cached data/NIFTY50.csv.
    Live deployments can inject a loader backed by market_data.fetch_history.
    Returns a pd.Series or None (the sizer fail-safes on None)."""
    try:
        import pandas as pd
        from pathlib import Path
        df = pd.read_csv(Path(__file__).parent / "data" / "NIFTY50.csv")
        return pd.Series(df["close"].values,
                         index=pd.to_datetime(df["date"])).sort_index()
    except Exception as e:
        log.warning("varma: NIFTY closes unavailable (%s) — sizer will fail-safe", e)
        return None


def varma_read(nifty_closes) -> tuple[float, str]:
    """One risk-state read → (factor, reason). Never raises, never > 1.0."""
    if not VARMA_SIZING:
        return 1.0, "varma sizing disabled"
    try:
        from varma_riskstate import exposure_factor
        r = exposure_factor(nifty_closes)          # its own fail-safe inside
        return float(min(r["factor"], 1.0)), r["reason"]
    except Exception as e:                          # module missing/broken
        return VARMA_FAILSAFE, f"varma module unavailable ({e}); fail-safe"


# ── time helpers ──────────────────────────────────────────────────────────────

def _next_boundary(now: datetime) -> datetime:
    m = (now.minute // CANDLE_MINUTES + 1) * CANDLE_MINUTES
    return (now.replace(minute=0, second=0, microsecond=0)
            + timedelta(minutes=m))


def _in_session(t: dtime) -> bool:
    return SESSION_OPEN < t <= SESSION_CLOSE


# ── demo: replay 2 synthetic sessions through the exact same pipeline ─────────

async def demo(days: int = 2, seed: int = 7) -> VwapMeanReversionBot:
    bot = VwapMeanReversionBot()
    feed = MockFeed(seed=seed)
    d = date.today()
    for day in range(days):
        session = d + timedelta(days=day)
        ts = datetime.combine(session, SESSION_OPEN)
        while True:
            ts = ts + timedelta(minutes=CANDLE_MINUTES)
            if ts.time() > SESSION_CLOSE:
                break
            await bot.on_candle(feed.next_candle(ts))
    await bot.notify.flush()          # drain queued alerts before loop closes
    bot.report()
    return bot


def main() -> int:
    ap = argparse.ArgumentParser(description="15m VWAP mean-reversion bot (mock)")
    ap.add_argument("--demo", action="store_true",
                    help="replay 2 synthetic sessions instantly and report")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    if args.demo:
        asyncio.run(demo(args.days, args.seed))
        return 0
    # Critical-crash guard: anything that escapes the loop (bugs, API auth
    # death, loop cancellation) alerts BEFORE the process dies. The notifier's
    # sync path (daemon thread) works here because the event loop is gone.
    try:
        asyncio.run(VwapMeanReversionBot().run(MockFeed(seed=args.seed)))
    except KeyboardInterrupt:
        notify_telegram.get_notifier().send("🔌 vwap_bot stopped by operator (Ctrl-C)")
    except Exception as e:
        log.exception("CRITICAL: bot crashed")
        notify_telegram.get_notifier().send(
            f"🚨 CRITICAL — vwap_bot CRASHED\n{type(e).__name__}: {e}\n"
            f"Process is DOWN; systemd will restart it if deployed per DEPLOY.md.")
        import time as _t
        _t.sleep(3)                    # give the daemon thread a beat to deliver
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
