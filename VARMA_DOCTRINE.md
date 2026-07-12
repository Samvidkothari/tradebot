# Varma Doctrine — risk & systematic-trading memory

*Distilled from the Dr. Samir Varma interview (particle physicist → ~30-yr
systematic futures trader). Durable knowledge for the research loops and any
agent working this repo. It states PRINCIPLES; the one mechanical rule built from
it so far is `varma_riskstate.py` (SPEC_varma_riskstate.md). Paper only.*

## First principles (the spine)

- **Trading is discipline, reaction, and risk control — not prediction.** Build
  reactive systems that respond to the current state; do not forecast returns.
- **Classify risk states qualitatively (high vs low), don't predict precisely.**
  A crude, explainable "this is a high-risk regime" beats a precise model that is
  *exactly* wrong in a crisis (GARCH/ARIMA fail catastrophically at the tails).
- **Returns are leptokurtic** — fat tails, more extremes than Gaussian implies.
  Standard deviation understates the left tail; never size off a sigma band alone.
- **Alpha is arbitraged away; being paid to bear risk is more durable.** Edge
  decays as competitors find the same signal. Design for that decay.
- **Markets are never fully efficient (Grossman–Stiglitz):** some inefficiency
  must remain to pay the people who make prices efficient. So edges exist — but
  they are small, crowded, and perishable.
- **Computational irreducibility:** simple deterministic rules can produce
  genuinely unpredictable output. Much price motion is deterministic *and*
  unpredictable. Accept the randomness; be humble.

## Risk & position sizing (the money rules)

- **Position sizing IS risk management.** Size off *acceptable drawdown*, not a
  fixed % rule applied blindly.
- **Use Kelly, but a fraction of it** — full Kelly is ruinous under fat tails and
  estimation error. Heavy de-risking is the default.
- **De-risk with regime, gradually.** Prefer a graded lean-out as risk builds over
  an on/off timer that whipsaws at one threshold.
- **Diversify toward negatively-correlated / uncorrelated sleeves** — they are
  rare and precious. (Maps to the fund blueprint's carry sleeve vs equity sleeves.)
- **Avoid rigid institutional mandates** (constant exposure, cookie-cutter DD
  limits, forced positions). Groupthink and bureaucracy cause bad risk-taking;
  a lean, client-aligned book can exploit that.

## Edge & process (how to earn confidence)

- **Find an edge congruent with your personality.** Counter-trend = high win-rate,
  rare large loss (for people who hate drawdowns). Trend-following = low win-rate,
  occasional huge win (for people who can endure losing streaks). Neither is
  "better"; the wrong one for you will not be survivable.
- **Losses teach more than wins.** Separate a *valid loss* (correct process, bad
  outcome) from a *bad-process* mistake. A lucky win from a bad process is the
  most dangerous outcome there is.
- **Pre-define entry, exit, and risk rules. Never decide at the moment of trade.**
- **Confidence in an edge = extensive backtesting + stress testing by trying to
  BREAK the system**, then iterate. "Flow/intuition" is legitimate only after
  massive validated experience — never a substitute for it.
- **Economics gives causation; finance alone gives correlation.** Prefer edges
  with an economic reason, not just a fitted correlation.
- **AI/ML aids pattern recognition and backtesting but must be directed and
  judged by human expertise** — it finds patterns, you decide which are real.

## Market microstructure (context; mostly intraday)

- Liquidity is thin at any instant; it exists over *time*, not at a point.
- **Stop-loss hunts & iceberg orders** are large execution algos exploiting
  clustered stops. Don't place stops at obvious levels (round numbers, prior
  highs/lows) where everyone else's sit.
- **"Leech on the whale":** when a large player is clearly pushing price, ride the
  same direction rather than fade it.
- NOTE for this repo: these are **intraday/order-flow** ideas. Intraday is dead
  here (cost lesson, frozen). Do not reopen it to chase these — they are context,
  not a mandate.

## How this maps to THIS repo

- `regime.py` already classifies trend / vol / character — the substrate for
  "classify, don't predict." `varma_riskstate.py` turns that read into a graded,
  fractional-Kelly-spirit exposure factor (strict generalization of the live
  binary `regime_overlay`).
- Fits the existing governance perfectly: pre-registration, one locked parameter
  set, after-cost/forward verdicts, default-reject, human-only promotion
  (`SELF_IMPROVE.md`). Keep it that way — do not tune the sizer to results.
- Congruent with `FUND_BLUEPRINT_2026-07-06.md`: portfolio-level vol targeting,
  drawdown circuit breakers, sleeve risk-budgeting, uncorrelated carry. Varma is
  the "why" behind those; the blueprint is the "how much."

## Standing cautions

- Do not let "classification" quietly become "prediction" (more axes, tuned
  weights = an overfit forecaster). Keep it few, transparent, locked.
- Regime reads lag; a graded sizer is a **brake, not a timer**. It will not dodge
  a one-day gap. Judge it over episodes.
- Every idea from this doctrine is still subject to the same gauntlet as anything
  else. Belief is not a backtest.
