# SPEC — Forward DEFINED-RISK Iron Condor PAPER test (NIFTY)

**Pre-registered:** 2026-06-16, before the condor simulator was written.
Implements the test proposed in `THESIS_condor.md`. **Paper-only, model-priced,
FORWARD** (no historical option data on free plan, so no backtest — and a
model-priced backtest cannot test the spread, which is the whole question).
NO real orders, ever. NO fully-autonomous live trading, ever (firm line: see
`[[options-discipline]]`).

**v2 amendment (2026-06-26) — fixed-duration entry (methodology fix, NOT a
result-driven tweak).** Same change adopted across both options books (see
`SPEC_options.md` v2 note): open only on a monthly expiry **≥ `OPEN_MIN_DTE = 21`
calendar days** out (roll to the next monthly otherwise), so cycles open with
~21–50 days of life and no near-worthless stubs. The condor's first cycle was
also a ~7-trading-day stub; v2 makes cycle durations comparable so the
head-to-head vs the strangle is fair. Changes ONLY entry timing — OTM %, wing
width, spread, sizing, and the INCONCLUSIVE-until-a-vol-event verdict gate are
unchanged. The forward book was reseeded fresh on adoption. Supersedes the v1
code constant `MIN_DTE = 7`.

## 1. What it is

A monthly **iron condor on NIFTY** — the naked short strangle with its tail
risk capped by two bought wings. Runs **side-by-side with the existing naked
strangle on the same days**, to answer one head-to-head question: *does giving
up the unlimited tail risk cost us the edge after honest costs, or not?*

- Each monthly cycle, four legs, hold to monthly expiry, settle at intrinsic:
  - **SELL** 1 OTM put + **SELL** 1 OTM call (the premium-harvesting "bodies")
  - **BUY** 1 deeper-OTM put + **BUY** 1 deeper-OTM call (the protective "wings")
- We keep the **net** credit if NIFTY stays between the short strikes; the wings
  cap the worst case to a known, finite number.

## 2. Instrument, data, pricing

- **Underlying:** NIFTY 50 (`^NSEI` live / `data/NIFTY50.csv`). Index options only.
- **No real IV available.** All four legs priced by **Black–Scholes using 20-day
  realized vol** as the IV proxy — same engine and same conservative-for-a-seller
  caveat as `SPEC_options.md` §2 (RV < real IV, so modeled premium understates a
  real seller's; it will not flatter the strategy).
- Monthly expiry = **last Thursday** of the month (NSE convention). `T` = trading
  days to expiry.

## 3. The rule (ONE pre-committed parameter set — not tuned to results)

- **Entry** (when flat and a new cycle is available), strikes rounded to nearest
  `STRIKE_STEP = 50`:
  - Short call nearest `spot × (1 + OTM_PCT)`, short put nearest
    `spot × (1 − OTM_PCT)`, with **`OTM_PCT = 0.04`** (4% OTM bodies).
  - Long call nearest `spot × (1 + WING_PCT)`, long put nearest
    `spot × (1 − WING_PCT)`, with **`WING_PCT = 0.06`** (6% OTM wings → a 2%-of-
    spot wing width, ≈ 500 pts at current NIFTY). *This is the locked choice; the
    medium wing. No re-tuning.*
- **Net premium received** = (short call + short put model prices) − (long call +
  long put model prices), **each leg less its spread haircut** (§4).
- **Hold to expiry:** settle all four legs at intrinsic (cash-settled; no spread
  on expiry settlement). **No early stop** — unlike the strangle, the bought
  wings *are* the risk management, so the structure's max loss is the cap. Adding
  a stop would be a redundant extra parameter (and curve-fitting surface).
- **Structural max loss** (informational, follows from the legs):
  `wing_width × LOT_SIZE − net_premium`, with `wing_width = (WING_PCT − OTM_PCT) ×
  spot ≈ 500 pts`. No gap can exceed it — that is the entire point.
- **Sizing:** `LOT_SIZE = 75` (NIFTY), **1 lot** per leg; `CAPITAL = ₹1,000,000`
  paper book. One position at a time.

## 4. Costs — the crux, deliberately HARSH, charged on ALL FOUR legs

- **Spread haircut = `SPREAD_PCT = 10%` of each leg's model price, per
  transaction**, charged on **entry** for all four legs (reduces net credit) and
  again on any leg closed before expiry. Identical harshness to `SPEC_options.md`
  §4 — but now paid on **four** legs, not two. This doubled bleed is the single
  biggest threat to the thesis and is modeled, not hidden.
- The wings are deeper OTM and thus *less liquid* in reality; we do **not** widen
  their haircut beyond 10% (we lack a calibrated number), so the test is if
  anything **optimistic** on wing spread — a caveat to state in the report.
- Plus standard statutory costs on premium turnover (brokerage ₹20/leg cap, STT,
  exchange txn, GST) — secondary to the spread.

## 5. Verdict rule (no premature conclusions)

- Report stays **"INCONCLUSIVE — awaiting a volatility event"** until the forward
  book has held a position through at least one genuine vol event: a session with
  `|NIFTY daily move| ≥ 4%`, OR a flagged budget/major-event day, while short.
  "Survives quiet months" is **not** evidence.
- No benchmark pass/fail gate (exploratory). The honest measures, judged only
  after a vol event:
  1. After the harsh 4-leg spread, does cumulative net P&L stay positive
     **through** the event?
  2. **Head-to-head vs the naked strangle on the same days:** how much edge did
     we give up in calm months, and how much disaster did we avoid in the event?
     The condor wins only if the avoided tail is worth the foregone premium.

## 6. Ledger & autonomy

- Separate SQLite **`condor.db`** (own `account`, `cycles`, `legs`/`marks`).
  Never touches `portfolio.db`, `intraday.db`, or the strangle's `options.db`.
- Runs daily unattended (open new cycle if flat; mark-to-model; settle at expiry)
  — pure simulation, places **no orders**.
- If this ever earned the right to go live it would be **semi-automatic: bot
  proposes, human approves** — never fully autonomous. The condor's finite worst
  case is the *only* reason a short-premium structure could ever be eligible at
  all; the naked strangle never can. See `[[options-discipline]]`.

## 7. Phase 2B compliance

- Structurally different from the naked strangle (defined-risk; four legs;
  different worst-case profile and a different other-side on the wings) — a new
  *structure*, not a re-tune. Thesis written first (`THESIS_condor.md`), spec
  committed **before** any simulator code runs. One parameter set; if it fails,
  the thesis failed — no result-driven re-tuning (`[[tradebot-constraints]]`).
