"""
research_assistant.py — a read-only AI research assistant.

A DETERMINISTIC, rule-based analyst (not a black-box LLM): it reads the research
artifacts the pipeline already produced (tear sheets with OOS split / walk-forward
/ Monte Carlo, factor leaderboards, the risk engine, the tech-debt register) and
emits findings — each with the numbers behind it and a recommendation. Every
recommendation is EXPLAINED; nothing is applied.

Responsibilities: review daily performance · analyse strategies · detect alpha
decay · detect overfitting · analyse factor performance · suggest improvements ·
review technical debt · generate a daily report.

HARD GUARANTEES (by construction, not policy):
  • It NEVER modifies a strategy. It has no write access to strategy specs/code
    and never calls a backtest/strategy mutator — it only reads JSON/markdown and
    writes results/research_assistant.{json,md}.
  • It places NO orders. There is no order path in this repo at all.
  • Every recommendation carries its evidence (the numbers) and a 'why'.

Thresholds are explicit module constants (documented, not tuned per result).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
BASE_DIR = Path(__file__).parent

# ── Explicit, documented heuristics (not fitted to any result) ────────────────
DECAY_WATCH_RATIO = 0.60     # OOS CAGR below 60% of full-period CAGR → watch
DECAY_SHARPE_RATIO = 0.50    # OOS Sharpe below 50% of full Sharpe → reinforces decay
OVERFIT_NEG_SEGMENTS = 2     # ≥2 negative walk-forward segments → instability
MC_PROB_NEG_WARN = 0.30      # Monte-Carlo P(CAGR<0) above 30% → fragile
DD_FAIL = -0.25              # max drawdown worse than −25% → fails the DD criterion
FACTOR_CROWDING = 4          # one symbol topping ≥4 factors → correlated signals

SEV_ORDER = {"warn": 0, "watch": 1, "info": 2, "good": 3}


def _load(name):
    fp = RESULTS_DIR / name
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text())
    except ValueError:
        return None


def _finding(area, severity, title, detail, recommendation, evidence=None):
    """One observation. `detail` is the explanation (the 'why'); `recommendation`
    is advisory only. `evidence` holds the numbers it is based on."""
    return {"area": area, "severity": severity, "title": title, "detail": detail,
            "recommendation": recommendation, "evidence": evidence or {}}


def _equity(strategies):
    return {k: s for k, s in strategies.items() if s.get("kind") == "equity"}


# ── Checks ────────────────────────────────────────────────────────────────────

def check_daily_performance(ts, risk):
    out = []
    rg = ts.get("regime") or {}
    if rg:
        out.append(_finding(
            "Daily performance", "info",
            f"Regime: {rg.get('trend')} · {rg.get('volatility')} · {rg.get('character')}",
            f"As of {rg.get('as_of')}. {rg.get('reason','')}",
            "Read the strategy findings below in the context of this regime — a "
            "strategy 'out of regime' is expected to lag now and that is not a fault.",
            {"tags": rg.get("tags")}))
    for k, s in _equity(ts.get("strategies", {})).items():
        f = s.get("full") or {}
        compat = (s.get("regime_compat") or {}).get("compatible")
        sev = "good" if compat else "watch"
        out.append(_finding(
            "Daily performance", sev, f"{s.get('label', k)} — full-period snapshot",
            f"CAGR {f.get('cagr', 0):.1%}, max drawdown {f.get('max_drawdown', 0):.1%}, "
            f"Sharpe {f.get('sharpe', 0):.2f}, alpha vs NIFTY {f.get('alpha', 0):+.1%}. "
            f"Currently {'in' if compat else 'out of'} the live regime.",
            "Continue paper-tracking; no action — performance review is observational."
            if compat else
            "Expect underperformance while out of regime; do not deploy on recent strength alone.",
            {"cagr": f.get("cagr"), "max_drawdown": f.get("max_drawdown"),
             "sharpe": f.get("sharpe"), "in_regime": bool(compat)}))
    if risk:
        sev = "warn" if risk.get("emergency") else ("watch" if risk.get("status") != "OK" else "good")
        out.append(_finding(
            "Daily performance", sev, f"Risk monitor: {risk.get('status')}",
            f"{risk.get('reason','')}. " + ("EMERGENCY flag raised." if risk.get("emergency")
                                            else "All hard limits within bounds."),
            "No action — the risk engine is a monitor; any breach would prompt a human, "
            "never an automatic halt." if not risk.get("emergency") else
            "Review the breached limit before the next paper cycle (still no auto-action).",
            {"status": risk.get("status"), "emergency": risk.get("emergency")}))
    return out


def check_alpha_decay(ts):
    out = []
    for k, s in _equity(ts.get("strategies", {})).items():
        f, o = s.get("full") or {}, s.get("oos") or {}
        fc, oc = f.get("cagr"), o.get("cagr")
        if fc is None or oc is None or fc == 0:
            continue
        cagr_ratio = oc / fc
        sh_ratio = (o.get("sharpe") / f["sharpe"]) if f.get("sharpe") else None
        if oc < 0 < fc:
            sev = "warn"
        elif cagr_ratio < DECAY_WATCH_RATIO or (sh_ratio is not None and sh_ratio < DECAY_SHARPE_RATIO):
            sev = "watch"
        else:
            sev = "good"
        detail = (f"Full-period CAGR {fc:.1%} vs out-of-sample (post-{o.get('start')}) "
                  f"{oc:.1%} — OOS is {cagr_ratio:.0%} of full. "
                  f"Sharpe {f.get('sharpe', 0):.2f} → {o.get('sharpe', 0):.2f}"
                  + (f" ({sh_ratio:.0%} retained)." if sh_ratio is not None else "."))
        rec = ("Edge looks intact out-of-sample — keep paper-tracking." if sev == "good" else
               "Material decay: keep on paper, do not size up; treat the in-sample edge as optimistic."
               if sev == "watch" else
               "Out-of-sample edge has effectively disappeared — keep retired/paper-only; "
               "do not deploy.")
        out.append(_finding("Alpha decay", sev, f"{s.get('label', k)} — IS vs OOS edge",
                            detail, rec,
                            {"full_cagr": fc, "oos_cagr": oc, "cagr_ratio": round(cagr_ratio, 2),
                             "sharpe_ratio": round(sh_ratio, 2) if sh_ratio is not None else None}))
    return out


def check_overfitting(ts):
    out = []
    for k, s in _equity(ts.get("strategies", {})).items():
        wf = s.get("walk_forward") or []
        mc = s.get("monte_carlo") or {}
        f, o = s.get("full") or {}, s.get("oos") or {}
        cagrs = [w.get("cagr", 0) for w in wf]
        neg = sum(1 for c in cagrs if c < 0)
        dispersion = (max(cagrs) - min(cagrs)) if cagrs else 0
        prob_neg = mc.get("prob_negative_cagr")
        decay = (o.get("cagr") / f["cagr"]) if f.get("cagr") else 1.0
        # Build the concern list against the SAME thresholds that set severity, so
        # the explanation never contradicts the colour.
        reasons = []
        if neg >= 1:
            reasons.append(f"{neg}/{len(wf)} walk-forward segments negative")
        if dispersion >= 0.5:
            reasons.append(f"wide segment dispersion ({dispersion:.0%} CAGR spread)")
        if prob_neg is not None and prob_neg > MC_PROB_NEG_WARN:
            reasons.append(f"Monte-Carlo P(CAGR<0) = {prob_neg:.0%}")
        if decay < 0.7:
            reasons.append(f"OOS only {decay:.0%} of in-sample")
        if neg >= OVERFIT_NEG_SEGMENTS:
            sev = "warn"
        elif len(reasons) >= 2 or (neg >= 1 and decay < 0.7):
            sev = "watch"
        else:
            sev = "good"
        detail = ("Walk-forward CAGRs " + str([round(c, 2) for c in cagrs]) +
                  (". Concerns: " + "; ".join(reasons) + "." if reasons else
                   ". Segments are consistent and Monte-Carlo dispersion is contained — "
                   "little sign of overfitting."))
        rec = ("Holds up across time slices — no overfitting action needed." if sev == "good" else
               "Some instability across regimes — keep one fixed parameter set (no re-tuning, "
               "per Phase 2B) and judge on OOS only." if sev == "watch" else
               "Strong regime dependence / instability — performance is likely period-specific; "
               "do not deploy and do not curve-fit new parameters to rescue it.")
        out.append(_finding("Overfitting", sev, f"{s.get('label', k)} — robustness across time",
                            detail, rec,
                            {"neg_segments": neg, "n_segments": len(wf),
                             "dispersion": round(dispersion, 2), "prob_negative_cagr": prob_neg,
                             "oos_ratio": round(decay, 2)}))
    return out


def check_factor_performance(factors):
    out = []
    if not factors or not factors.get("factors"):
        return out
    # Crowding: how often a single symbol tops the factors (correlated signals).
    tops = {}
    for v in factors["factors"].values():
        sym = (v.get("top") or [{}])[0].get("symbol")
        if sym:
            tops[sym] = tops.get(sym, 0) + 1
    if tops:
        sym, n = max(tops.items(), key=lambda kv: kv[1])
        if n >= FACTOR_CROWDING:
            out.append(_finding(
                "Factor performance", "watch", f"Signal crowding on {sym}",
                f"{sym} is the top-ranked name in {n} of {len(factors['factors'])} factors — "
                "those factors are picking the same bet, so blending them adds less "
                "diversification than the count suggests.",
                "When combining factors, down-weight or orthogonalise the correlated ones "
                "(e.g. beta/correlation/trend-persistence move together); do not treat each "
                "as an independent signal.",
                {"symbol": sym, "factors_topped": n, "n_factors": len(factors["factors"])}))
    unavailable = factors.get("unavailable") or []
    if unavailable:
        out.append(_finding(
            "Factor performance", "info", f"{len(unavailable)} factors blocked by data scope",
            "These need a fundamentals/quality feed the free plan lacks: "
            + ", ".join(unavailable[:6]) + ("…" if len(unavailable) > 6 else "") + ".",
            "Adding a fundamentals feed would unlock value/quality factors — the most "
            "likely source of a genuinely new edge. Until then, do not fabricate them.",
            {"unavailable": unavailable}))
    out.append(_finding(
        "Factor performance", "good", "Factor library breadth",
        f"{len(factors['factors'])} price/volume factors active across technical, "
        "statistical and market groups.",
        "Healthy breadth; focus future work on orthogonality and the fundamentals gap "
        "rather than adding more correlated price factors.",
        {"n_factors": len(factors["factors"])}))
    return out


def check_tech_debt():
    out = []
    fp = BASE_DIR / "TECH_DEBT.md"
    if not fp.exists():
        return out
    txt = fp.read_text()
    resolved = len(re.findall(r"^\s*-\s*✅", txt, re.M))
    open_items = re.findall(r"^###\s+\d+\.\s+(.+)$", txt, re.M)
    wont_fix = len(re.findall(r"WON'T FIX", txt))
    out.append(_finding(
        "Technical debt", "info", f"{len(open_items)} open debt items on the register",
        f"{resolved} items resolved; {len(open_items)} open ({wont_fix} consciously WON'T-FIX). "
        + ("Open: " + "; ".join(t.strip() for t in open_items[:4]) + "." if open_items else ""),
        "The register already records a decision per item — keep optimising for robustness, "
        "not complexity; never refactor pre-registered strategy code (committed verdicts must "
        "stay byte-identical). No action unless a debt starts causing bugs.",
        {"resolved": resolved, "open": len(open_items), "wont_fix": wont_fix}))
    return out


def synthesize_improvements(findings):
    """Roll the most actionable findings into a short, explained improvement list.
    Suggestions only — never applied."""
    out = []
    by_area = {}
    for f in findings:
        by_area.setdefault(f["area"], []).append(f)
    warns = [f for f in findings if f["severity"] == "warn"]
    watches = [f for f in findings if f["severity"] == "watch"]
    if warns:
        out.append(_finding(
            "Improvements", "warn", "Address the flagged strategies first",
            "These crossed a hard heuristic: " + "; ".join(f"{f['title']}" for f in warns) + ".",
            "Keep them retired/paper-only and resist re-tuning to rescue them — a curve-fit "
            "fix would just relocate the overfitting. " + " ".join(f["recommendation"] for f in warns[:2]),
            {"count": len(warns)}))
    if not warns and not watches:
        out.append(_finding(
            "Improvements", "good", "No structural concerns this run",
            "No strategy crossed a decay/overfitting threshold and risk is within limits.",
            "Best next edge is widening the data scope (fundamentals) rather than adding "
            "more correlated price factors or new parameter sweeps.",
            {}))
    out.append(_finding(
        "Improvements", "info", "Highest-leverage next step",
        "The recurring ceiling is data scope: value/quality factors and per-stock "
        "surveillance/circuit data are unavailable on the free plan.",
        "Evaluate a fundamentals feed before more strategy search — the 2-class equity "
        "budget is closed and the thin price-only edges are decaying. This is a suggestion; "
        "no change is made automatically.",
        {}))
    return out


# ── Report assembly ───────────────────────────────────────────────────────────

def build_report():
    ts = _load("tearsheets.json") or {}
    factors = _load("factors.json")
    risk = _load("risk_engine.json")

    findings = []
    findings += check_daily_performance(ts, risk)
    findings += check_alpha_decay(ts)
    findings += check_overfitting(ts)
    findings += check_factor_performance(factors)
    findings += check_tech_debt()
    findings += synthesize_improvements(findings)
    findings.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["area"]))

    summary = {s: sum(1 for f in findings if f["severity"] == s)
               for s in ("warn", "watch", "info", "good")}
    return {
        "generated": date.today().isoformat(),
        "as_of": (ts.get("regime") or {}).get("as_of"),
        "headline": _headline(summary),
        "summary": summary,
        "findings": findings,
        "disclaimer": ("Read-only analyst. Never modifies strategies, never places "
                       "orders. Every recommendation is advisory and explained."),
    }


def _headline(summary):
    if summary["warn"]:
        return f"{summary['warn']} item(s) need attention (paper-only — no auto-action)."
    if summary["watch"]:
        return f"{summary['watch']} item(s) to watch; nothing breached a hard limit."
    return "No structural concerns this run."


def render_markdown(rep) -> str:
    icon = {"warn": "🔴", "watch": "🟡", "info": "🔵", "good": "🟢"}
    L = [f"# AI Research Assistant — Daily Review ({rep['generated']})", "",
         f"_{rep['disclaimer']}_", "",
         f"**{rep['headline']}**  ", "",
         f"🔴 {rep['summary']['warn']} · 🟡 {rep['summary']['watch']} · "
         f"🔵 {rep['summary']['info']} · 🟢 {rep['summary']['good']}", ""]
    area = None
    for f in sorted(rep["findings"], key=lambda x: (x["area"], SEV_ORDER.get(x["severity"], 9))):
        if f["area"] != area:
            area = f["area"]
            L += ["", f"## {area}", ""]
        L += [f"### {icon.get(f['severity'], '•')} {f['title']}",
              f"{f['detail']}", "",
              f"**Recommendation:** {f['recommendation']}", ""]
    L += ["---", "_Generated by `research_assistant.py` — deterministic, rule-based, "
          "read-only. No strategy was modified; no order was placed._"]
    return "\n".join(L)


def main():
    import schemas
    rep = build_report()
    schemas.validate("research_assistant.json", rep)   # fail loudly on contract drift
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "research_assistant.json").write_text(json.dumps(rep, indent=2))
    (RESULTS_DIR / "research_assistant.md").write_text(render_markdown(rep))
    print(f"AI research assistant: {rep['headline']}")
    print(f"  {len(rep['findings'])} findings — "
          f"🔴{rep['summary']['warn']} 🟡{rep['summary']['watch']} "
          f"🔵{rep['summary']['info']} 🟢{rep['summary']['good']}")
    print("  → results/research_assistant.{json,md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
