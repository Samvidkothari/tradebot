"""
backtest_momentum.py — Portfolio-level cross-sectional momentum backtest.

Implements EXACTLY the pre-registered design in strategies/SPEC_momentum.md.
Pre-registration commit: cd1b698. No parameter here may be tuned to results
(Phase 2B Rule 3). This runs ONCE and the verdict is committed either way.

Engine summary (per the spec):
  • Universe: all NIFTY 50 CSVs in data/ EXCEPT the NIFTY50 index itself.
  • Rebalance: first trading day of each calendar month, after the 273-day warmup.
  • Holdings: top 15 by 12-1 momentum, equal-weight (1/15 each).
  • Costs: turnover-aware — bought_value*COST_ENTRY + sold_value*COST_EXIT,
    using the SAME cost constants as the SMA backtest.
  • Valuation: ffill'd closes for daily mark-to-market; RAW closes for ranking
    (a stock with a data gap on a rebalance day is simply not rankable).
  • Out-of-sample split at 2024-01-01; benchmark = NIFTY 50 buy-and-hold.

Output:
  • terminal summary
  • results/momentum_report.md
"""

from datetime import date
from pathlib import Path

import pandas as pd

# Reuse the IDENTICAL cost model and benchmark/formatting helpers from the SMA
# backtest — the spec mandates "same cost model, same data, same OOS split".
from backtest import (
    COST_ENTRY, COST_EXIT, COST_ROUNDTRIP,
    bnh_metrics, _p, _pp,
)
from momentum import (
    momentum_scores, target_portfolio,
    FORMATION, SKIP, LOOKBACK, TOP_N,
)

DATA_DIR    = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"
SPLIT_DATE  = "2024-01-01"     # Period B (out-of-sample) starts here
STARTING    = 1.0              # normalised capital; costs are fractions of value


# ── Data loading ──────────────────────────────────────────────────────────────

def load_panel():
    """
    Build a daily-close panel from every stock CSV in data/ except the index.

    Returns
      panel_raw : DataFrame (index=dates, cols=symbols) of RAW closes (NaN gaps)
      nifty_df  : the NIFTY50 benchmark DataFrame (date/close)
    """
    closes = {}
    nifty_df = None
    for fp in sorted(DATA_DIR.glob("*.csv")):
        sym = fp.stem
        df = pd.read_csv(fp, parse_dates=["date"]).sort_values("date")
        if sym == "NIFTY50":
            nifty_df = df.reset_index(drop=True)
            continue
        closes[sym] = df.set_index("date")["close"]

    if nifty_df is None:
        raise FileNotFoundError("data/NIFTY50.csv not found — run fetch_data.py")

    panel_raw = pd.DataFrame(closes).sort_index()
    return panel_raw, nifty_df


def rebalance_dates(panel):
    """First trading day of each calendar month, once past the 273-day warmup."""
    first_of_month = {}
    for d in panel.index:
        key = (d.year, d.month)
        if key not in first_of_month:           # index is sorted → first = earliest
            first_of_month[key] = d
    return [d for d in first_of_month.values()
            if panel.index.get_loc(d) >= LOOKBACK]


# ── Backtest engine ───────────────────────────────────────────────────────────

def run_momentum(panel_raw):
    """
    Simulate the monthly-rebalanced top-15 equal-weight momentum portfolio.

    Returns
      equity   : pd.Series (date index) of portfolio value, starts at STARTING
      n_changes: int — total position-changes (entries + exits) across the run
      history  : list of per-rebalance dicts (date, holdings, turnover, cost)
    """
    panel_val = panel_raw.ffill()               # valuation prices (gaps carried fwd)
    rebals    = rebalance_dates(panel_raw)
    if not rebals:
        return pd.Series(dtype=float), 0, []

    first_rebal = rebals[0]
    rebal_set   = set(rebals)

    shares    = {}          # symbol -> share count currently held
    cash      = STARTING
    prev_held = set()
    entries = exits = 0
    history = []
    equity  = {}

    days = panel_raw.index[panel_raw.index >= first_rebal]
    for day in days:
        px = panel_val.loc[day]

        if day in rebal_set:
            # Mark the book to today's prices BEFORE trading.
            pv = cash + sum(sh * px[s] for s, sh in shares.items())

            target = target_portfolio(panel_raw, day, top_n=TOP_N)
            names  = set(shares) | set(target)
            cur_val = {s: shares.get(s, 0) * px[s] for s in names}

            # Two-pass cost: estimate turnover at equal target, deduct, re-target,
            # so we never "trade cash that isn't there" (spec §4).
            tgt_each0 = pv / TOP_N
            bought = sum(max((tgt_each0 if s in target else 0) - cur_val[s], 0)
                         for s in names)
            sold   = sum(max(cur_val[s] - (tgt_each0 if s in target else 0), 0)
                         for s in names)
            cost   = bought * COST_ENTRY + sold * COST_EXIT

            invest   = pv - cost
            tgt_each = invest / TOP_N           # equal-weight; fewer names → cash drag

            new_shares = {}
            for s in target:
                price = px[s]
                if pd.notna(price) and price > 0:
                    new_shares[s] = tgt_each / price

            new_held = set(new_shares)
            entries += len(new_held - prev_held)
            exits   += len(prev_held - new_held)

            invested = tgt_each * len(new_shares)
            cash      = pv - invested - cost     # remainder (if < TOP_N names) stays cash
            shares    = new_shares
            prev_held = new_held

            history.append(dict(date=day, n_holdings=len(new_shares),
                                 bought=bought, sold=sold, cost=cost))

        pv = cash + sum(sh * px[s] for s, sh in shares.items())
        equity[day] = pv

    return pd.Series(equity).sort_index(), entries + exits, history


# ── Metrics ───────────────────────────────────────────────────────────────────

def equity_metrics(equity, start=None, end=None):
    """CAGR / total / max-drawdown for an equity slice, renormalised to 1.0."""
    eq = equity.copy()
    if start:
        eq = eq[eq.index >= pd.Timestamp(start)]
    if end:
        eq = eq[eq.index < pd.Timestamp(end)]
    if eq.empty or len(eq) < 2:
        return None
    eq = eq / eq.iloc[0]

    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years <= 0:
        return None
    rmax = eq.cummax()
    dd   = (eq - rmax) / rmax
    return dict(
        total_ret=float(eq.iloc[-1] - 1),
        cagr=float(eq.iloc[-1] ** (1 / years) - 1),
        max_dd=float(dd.min()),
        years=round(years, 1),
    )


# ── PASS / FAIL evaluation (criteria pre-committed in the spec) ────────────────

def evaluate(mom_full, mom_b, nifty_full, nifty_b, n_changes):
    c1 = (mom_full and nifty_full and mom_b and nifty_b
          and mom_full["cagr"] > nifty_full["cagr"]
          and mom_b["cagr"]    > nifty_b["cagr"])
    c2 = (mom_full and nifty_full and mom_full["max_dd"] >= nifty_full["max_dd"])
    c3 = n_changes >= 30
    return dict(c1=bool(c1), c2=bool(c2), c3=bool(c3),
                passed=bool(c1 and c2 and c3))


# ── Reporting ─────────────────────────────────────────────────────────────────

def _cmp_line(label, mom, nifty):
    if not mom:
        return f"  {label:<22} (insufficient data)"
    nstr = (f"   vs NIFTY {_p(nifty['cagr'])} CAGR / {_p(nifty['max_dd'])} DD"
            if nifty else "")
    return (f"  {label:<22} CAGR {_p(mom['cagr'])}   Total {_p(mom['total_ret'])}"
            f"   MaxDD {_p(mom['max_dd'])}{nstr}")


def print_summary(mom_full, mom_a, mom_b, nifty_full, nifty_a, nifty_b,
                  n_changes, verdict):
    W = 92
    print(f"\n{'='*W}")
    print("  CROSS-SECTIONAL MOMENTUM (12-1) — NIFTY 50, monthly rebalance, top-15 eq-wt")
    print(f"{'='*W}")
    print(_cmp_line("Full period", mom_full, nifty_full))
    print(_cmp_line(f"Period A (pre {SPLIT_DATE[:7]})", mom_a, nifty_a))
    print(_cmp_line(f"Period B (OOS, {SPLIT_DATE[:7]}+)", mom_b, nifty_b))
    print(f"\n  Position-changes (entries+exits): {n_changes}")
    print(f"\n{'─'*W}")
    print("  PASS CRITERIA (all must hold):")
    print(f"    [{'PASS' if verdict['c1'] else 'FAIL'}] 1. Beat NIFTY CAGR in FULL and Period B")
    print(f"    [{'PASS' if verdict['c2'] else 'FAIL'}] 2. Max drawdown no worse than NIFTY's")
    print(f"    [{'PASS' if verdict['c3'] else 'FAIL'}] 3. >= 30 position-changes  (got {n_changes})")
    print(f"\n  VERDICT: {'PASS' if verdict['passed'] else 'FAIL'}")
    print(f"{'='*W}\n")


def save_report(mom_full, mom_a, mom_b, nifty_full, nifty_a, nifty_b,
                n_changes, verdict, n_universe, history):
    RESULTS_DIR.mkdir(exist_ok=True)
    L = []
    L.append("# Cross-Sectional Momentum (12-1) — Backtest Report\n\n")
    L.append(f"Generated: {date.today()}  \n")
    L.append("Spec: `strategies/SPEC_momentum.md` (pre-registered, commit cd1b698).  \n")
    L.append(f"Strategy: monthly rebalance into the top {TOP_N} NIFTY 50 names by "
             f"12-1 momentum (skip most-recent month), equal-weight.  \n")
    L.append(f"Universe: {n_universe} stocks (survivorship-biased — current members "
             f"only; see spec §2).  \n")
    L.append(f"Lookback {LOOKBACK}d (formation {FORMATION} + skip {SKIP}); "
             f"round-trip cost ≈{COST_ROUNDTRIP*100:.3f}%, charged on turnover.  \n")

    def block(title, mom, nifty):
        out = [f"\n## {title}\n\n",
               "| Metric | Momentum | NIFTY 50 B&H |\n|---|---|---|\n"]
        if mom:
            out.append(f"| CAGR | {_p(mom['cagr'])} | "
                       f"{_p(nifty['cagr']) if nifty else 'n/a'} |\n")
            out.append(f"| Total return | {_p(mom['total_ret'])} | "
                       f"{_p(nifty['total_ret']) if nifty else 'n/a'} |\n")
            out.append(f"| Max drawdown | {_p(mom['max_dd'])} | "
                       f"{_p(nifty['max_dd']) if nifty else 'n/a'} |\n")
            out.append(f"| Years | {mom['years']} | "
                       f"{nifty['years'] if nifty else 'n/a'} |\n")
        else:
            out.append("| (insufficient data) | | |\n")
        return out

    L += block("Full Period", mom_full, nifty_full)
    L += block(f"Period A — before {SPLIT_DATE[:7]} (in-sample proxy)", mom_a, nifty_a)
    L += block(f"Period B — from {SPLIT_DATE[:7]} (out-of-sample)", mom_b, nifty_b)

    L.append("\n## Pre-Committed PASS Criteria\n\n")
    L.append("| # | Criterion | Result |\n|---|---|---|\n")
    L.append(f"| 1 | Beat NIFTY CAGR in FULL **and** Period B | "
             f"{'PASS' if verdict['c1'] else 'FAIL'} |\n")
    L.append(f"| 2 | Max drawdown no worse than NIFTY's | "
             f"{'PASS' if verdict['c2'] else 'FAIL'} |\n")
    L.append(f"| 3 | >= 30 position-changes (got {n_changes}) | "
             f"{'PASS' if verdict['c3'] else 'FAIL'} |\n")
    L.append(f"\n**VERDICT: {'PASS' if verdict['passed'] else 'FAIL'}**\n")

    L.append("\n## Honest Assessment\n\n")
    if mom_full and nifty_full:
        beat_full = mom_full["cagr"] > nifty_full["cagr"]
        beat_b    = bool(mom_b and nifty_b and mom_b["cagr"] > nifty_b["cagr"])
        L.append(f"- **Full period:** momentum {_p(mom_full['cagr'])} CAGR vs NIFTY "
                 f"{_p(nifty_full['cagr'])} — {'beat' if beat_full else 'lost to'} "
                 f"buy-and-hold after all costs.\n")
        L.append(f"- **Out-of-sample (Period B):** momentum {_p(mom_b['cagr']) if mom_b else 'n/a'} "
                 f"vs NIFTY {_p(nifty_b['cagr']) if nifty_b else 'n/a'} — "
                 f"{'held up' if beat_b else 'did not beat the index'}.\n")
        L.append(f"- **Drawdown:** worst momentum drawdown {_p(mom_full['max_dd'])} "
                 f"vs NIFTY {_p(nifty_full['max_dd'])}.\n")
    L.append(f"- **Activity:** {n_changes} position-changes across {len(history)} "
             f"monthly rebalances.\n")
    L.append("- **Survivorship bias:** today's NIFTY 50 membership applied to the past; "
             "a true point-in-time test would likely be somewhat worse (spec §2).\n")
    L.append("- **Standing benchmark:** NIFTY 50 buy-and-hold delivers ≈+8% CAGR with "
             "roughly half the drawdown for zero effort. That is the bar.\n")
    if not verdict["passed"]:
        L.append("- **Phase 2B accounting:** this is one of the two permitted "
                 "strategy-class attempts. One attempt remains.\n")

    path = RESULTS_DIR / "momentum_report.md"
    path.write_text("".join(L))
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    panel_raw, nifty_df = load_panel()
    n_universe = panel_raw.shape[1]

    print(f"\nLoaded {n_universe} stocks  "
          f"({panel_raw.index.min().date()} → {panel_raw.index.max().date()}, "
          f"{len(panel_raw)} trading days)")
    print(f"Warmup: {LOOKBACK} days; rebalancing monthly, top {TOP_N} equal-weight.")
    print("Running momentum backtest...", end=" ", flush=True)

    equity, n_changes, history = run_momentum(panel_raw)
    print("done")

    mom_full = equity_metrics(equity)
    mom_a    = equity_metrics(equity, end=SPLIT_DATE)
    mom_b    = equity_metrics(equity, start=SPLIT_DATE)

    nifty_full = bnh_metrics(nifty_df, start=equity.index[0])
    nifty_a    = bnh_metrics(nifty_df, start=equity.index[0], end=SPLIT_DATE)
    nifty_b    = bnh_metrics(nifty_df, start=SPLIT_DATE)

    verdict = evaluate(mom_full, mom_b, nifty_full, nifty_b, n_changes)

    print_summary(mom_full, mom_a, mom_b, nifty_full, nifty_a, nifty_b,
                  n_changes, verdict)

    path = save_report(mom_full, mom_a, mom_b, nifty_full, nifty_a, nifty_b,
                       n_changes, verdict, n_universe, history)
    print(f"  Report saved → {path}\n")


if __name__ == "__main__":
    main()
