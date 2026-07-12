"""pattern_isolation.py — automated edge-decay isolation (Pillar 3).

The weekly-runnable pandas parser the Operational Edge Framework asks for: given
the tagged trade log (trade_journal.py), slice performance by every context axis
and FLAG where the edge has weakened or disappeared. This is the daily-bar,
statistically-honest version of "the edge dies after 11:30 AM ET" — here the axes
are setup_type, day-of-week, regime, quarter, holding-period bucket, and RECENCY.

Two kinds of flag:
  • bucket flag  — a level of some dimension whose after-cost expectancy is
                   negative on a meaningful sample (n >= MIN_N).
  • decay flag   — the whole book (or a setup) that was profitable overall but is
                   negative over the most recent RECENT_N trades: an edge fading in
                   real time. This is the one that matters most.

Pure pandas over the trade log — no I/O beyond an optional CSV read, no orders.
Run it weekly: `python pattern_isolation.py results/trade_log.csv`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

MIN_N = 20          # min trades before a bucket verdict is trusted
RECENT_N = 30       # trailing-trades window for the recency/decay check
DIMENSIONS = ["setup_type", "dow", "regime", "quarter", "hold_bucket"]


def _stats(R: pd.Series) -> dict:
    R = R.dropna()
    n = len(R)
    if n == 0:
        return {"n": 0, "expectancy": np.nan, "win_rate": np.nan,
                "profit_factor": np.nan, "sharpe_R": np.nan}
    wins, losses = R[R > 0], R[R <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else np.inf
    sd = R.std(ddof=1)
    return {"n": int(n), "expectancy": float(R.mean()),
            "win_rate": float((R > 0).mean()), "profit_factor": float(pf),
            "sharpe_R": float(R.mean() / sd) if (n > 1 and sd > 0) else 0.0}


def _hold_bucket(days) -> str:
    d = float(days)
    if d <= 3:
        return "0-3d"
    if d <= 10:
        return "4-10d"
    if d <= 30:
        return "11-30d"
    return "30d+"


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the derived columns pattern isolation needs exist."""
    df = df.copy()
    if "hold_bucket" not in df.columns:
        df["hold_bucket"] = df.get("hold_bars", 0).map(_hold_bucket)
    if "exit_date" in df.columns:
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        df = df.sort_values("exit_date")
    return df


def by_dimension(df: pd.DataFrame, dim: str) -> pd.DataFrame:
    """Per-level after-cost stats for one dimension, sorted worst-expectancy first."""
    rows = []
    for level, g in df.groupby(dim):
        s = _stats(g["R"])
        s[dim] = level
        rows.append(s)
    out = pd.DataFrame(rows)
    return out.sort_values("expectancy").reset_index(drop=True) if len(out) else out


def decay_check(df: pd.DataFrame, recent_n: int = RECENT_N) -> dict:
    """Is the edge fading? Compares full-sample vs most-recent-N expectancy for the
    whole book and per setup_type. Flags any that flipped positive → negative."""
    df = df.sort_values("exit_date") if "exit_date" in df.columns else df
    flags = []

    def _cmp(label, g):
        full = _stats(g["R"])
        recent = _stats(g["R"].tail(recent_n))
        fading = (full["n"] >= MIN_N and recent["n"] >= max(10, recent_n // 2)
                  and full["expectancy"] > 0 and recent["expectancy"] < 0)
        if fading:
            flags.append({"scope": label, "full_expectancy": round(full["expectancy"], 3),
                          "recent_expectancy": round(recent["expectancy"], 3),
                          "recent_n": recent["n"]})
        return full, recent

    book_full, book_recent = _cmp("ALL", df)
    per_setup = {}
    for st, g in df.groupby("setup_type"):
        f, r = _cmp(f"setup:{st}", g)
        per_setup[st] = {"full": f, "recent": r}
    return {"book_full": book_full, "book_recent": book_recent,
            "per_setup": per_setup, "decay_flags": flags}


def weekly_report(df: pd.DataFrame) -> dict:
    """The full weekly pass: bucket flags (negative-expectancy levels with n>=MIN_N)
    across all dimensions + the recency decay check. Returns a structured dict."""
    df = prepare(df)
    bucket_flags = []
    for dim in DIMENSIONS:
        if dim not in df.columns:
            continue
        t = by_dimension(df, dim)
        for _, r in t.iterrows():
            if r["n"] >= MIN_N and r["expectancy"] < 0:
                bucket_flags.append({"dimension": dim, "level": r[dim],
                                     "n": int(r["n"]),
                                     "expectancy": round(float(r["expectancy"]), 3),
                                     "profit_factor": round(float(r["profit_factor"]), 2)})
    decay = decay_check(df)
    return {"n_trades": int(len(df)), "overall": _stats(df["R"]),
            "bucket_flags": bucket_flags, "decay_flags": decay["decay_flags"],
            "decay_detail": {"book_full_exp": round(decay["book_full"]["expectancy"], 3)
                             if decay["book_full"]["n"] else None,
                             "book_recent_exp": round(decay["book_recent"]["expectancy"], 3)
                             if decay["book_recent"]["n"] else None}}


def format_report(rep: dict) -> str:
    """Human/markdown summary of weekly_report output."""
    L = [f"# Pattern Isolation — {rep['n_trades']} trades",
         f"\nOverall: expectancy {rep['overall']['expectancy']:+.3f}R · "
         f"PF {rep['overall']['profit_factor']:.2f} · "
         f"win {rep['overall']['win_rate']*100:.0f}% (n={rep['overall']['n']})\n"]
    if rep["decay_flags"]:
        L.append("\n## ⚠ EDGE DECAY (recent < 0 while full > 0)\n")
        for f in rep["decay_flags"]:
            L.append(f"- **{f['scope']}**: full {f['full_expectancy']:+.3f}R → "
                     f"recent {f['recent_expectancy']:+.3f}R (last {f['recent_n']})")
    else:
        L.append("\nNo recency decay flags.")
    if rep["bucket_flags"]:
        L.append("\n## Negative-expectancy buckets (n ≥ %d)\n" % MIN_N)
        for f in rep["bucket_flags"]:
            L.append(f"- {f['dimension']}={f['level']}: {f['expectancy']:+.3f}R "
                     f"(n={f['n']}, PF {f['profit_factor']})")
    else:
        L.append("\nNo negative-expectancy buckets above the sample floor.")
    return "\n".join(L)


def main():  # pragma: no cover
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "results/trade_log.csv"
    if not Path(path).exists():
        print(f"no trade log at {path} — emit one via trade_journal.append_csv")
        return
    df = pd.read_csv(path)
    print(format_report(weekly_report(df)))


if __name__ == "__main__":  # pragma: no cover
    main()
