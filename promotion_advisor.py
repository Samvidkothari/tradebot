"""
promotion_advisor.py — automated strategy lifecycle evaluator (Level 3).

Closes the research → trading loop with pre-registered rules instead of vibes.
Every daily run it reads the research pipeline's evidence (results/
tearsheets.json), scores each strategy book against promotion_rules.json, and
writes verdicts + the evidence used to results/promotion_advice.json:

  KEEP     enabled and passing every rule
  PROMOTE  disabled but passing every rule (candidate to switch on)
  RETIRE   enabled but breaching a hard rule (drawdown / negative OOS)
  WATCH    not enough evidence yet (short window, few closed cycles)

Execution is opt-in: with auto_execute=false (default) this is advice a human
acts on from the dashboard. With auto_execute=true it flips the SAME dashboard
enable/disable flags a human would (controls.set_enabled), capped at
max_changes_per_run per day, and appends every action to
results/promotion_log.json — an auditable decision trail either way.

Deliberate boundaries (why this can't become an overfitting machine):
  • Rules are pre-registered and frozen; the advisor cannot tune them.
  • It cannot change a strategy's parameters — only on/off.
  • Code-level RETIRED flags (options_sim.py) remain human-only.
RESEARCH/PAPER ONLY — flips paper-book flags; places no orders.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
RESULTS = BASE_DIR / "results"
RULES_PATH = BASE_DIR / "promotion_rules.json"

# tearsheet strategy key → controls.py STRATEGIES key (the on/off surface)
CONTROL_KEY = {"lowvol": "lowvol", "momentum": "momentum",
               "strangle": "strangle", "condor": "condor",
               "llm": "llm", "intraday": "intraday"}


def load_rules(path: Path = RULES_PATH) -> dict:
    rules = {k: v for k, v in json.loads(Path(path).read_text()).items()
             if not k.startswith("_")}
    rules.setdefault("auto_execute", False)
    rules.setdefault("max_changes_per_run", 1)
    return rules


def _equity_verdict(name: str, ts: dict, rules: dict, enabled: bool) -> dict:
    """Judge an equity-curve strategy on its out-of-sample + walk-forward record."""
    oos = ts.get("oos") or {}
    wf = ts.get("walk_forward") or []
    evidence, fails = {}, []

    n_days = oos.get("n_days") or 0
    evidence["oos_days"] = n_days
    if n_days < rules["min_oos_days"]:
        return {"book": name, "verdict": "WATCH", "enabled": enabled,
                "reason": f"only {n_days} OOS days (< {rules['min_oos_days']})",
                "evidence": evidence}

    dd = oos.get("max_drawdown")
    evidence["oos_max_drawdown"] = dd
    if dd is not None and dd <= rules["max_oos_drawdown"]:
        fails.append(f"OOS drawdown {dd:.1%} breaches {rules['max_oos_drawdown']:.0%}")

    sharpe = oos.get("sharpe")
    evidence["oos_sharpe"] = sharpe
    if sharpe is not None and sharpe < rules["min_oos_sharpe"]:
        fails.append(f"OOS Sharpe {sharpe:.2f} < {rules['min_oos_sharpe']}")

    tot = oos.get("total_return")
    evidence["oos_total_return"] = tot
    if tot is not None and tot < 0:
        fails.append(f"negative OOS return {tot:.1%}")

    if wf:
        pos = sum(1 for seg in wf if (seg.get("cagr") or 0) > 0) / len(wf)
        evidence["walk_forward_positive_frac"] = round(pos, 2)
        if pos < rules["min_walk_forward_positive_frac"]:
            fails.append(f"walk-forward: only {pos:.0%} of segments positive")

    if fails:
        verdict = "RETIRE" if enabled else "WATCH"
        return {"book": name, "verdict": verdict, "enabled": enabled,
                "reason": "; ".join(fails), "evidence": evidence}
    return {"book": name, "verdict": "KEEP" if enabled else "PROMOTE",
            "enabled": enabled, "reason": "passes every pre-registered rule",
            "evidence": evidence}


def _options_verdict(name: str, ts: dict, rules: dict, enabled: bool) -> dict:
    """Options books: judged on their actual paper-ledger track record."""
    n_closed = ts.get("n_closed") or 0
    evidence = {"n_closed_cycles": n_closed,
                "realised": ts.get("realised"), "unrealised": ts.get("unrealised")}
    if n_closed < rules["min_closed_cycles"]:
        return {"book": name, "verdict": "WATCH", "enabled": enabled,
                "reason": f"only {n_closed} closed cycles "
                          f"(< {rules['min_closed_cycles']}) — keep collecting evidence",
                "evidence": evidence}
    pnl = (ts.get("realised") or 0) + (ts.get("unrealised") or 0)
    evidence["total_pnl"] = pnl
    if pnl < 0 and enabled:
        return {"book": name, "verdict": "RETIRE", "enabled": enabled,
                "reason": f"negative track record after {n_closed} cycles",
                "evidence": evidence}
    return {"book": name, "verdict": "KEEP" if enabled else "PROMOTE",
            "enabled": enabled, "reason": "track record acceptable",
            "evidence": evidence}


def evaluate(strategies: dict, rules: dict, flags: dict) -> list[dict]:
    """Pure decision core: tearsheet strategies + rules + current flags → verdicts."""
    out = []
    for name, ts in strategies.items():
        key = CONTROL_KEY.get(name)
        enabled = flags.get(key, True) if key else True
        if ts.get("kind") == "options":
            v = _options_verdict(name, ts, rules, enabled)
        elif "oos" in ts:
            v = _equity_verdict(name, ts, rules, enabled)
        else:
            v = {"book": name, "verdict": "WATCH", "enabled": enabled,
                 "reason": "no evaluable evidence in tearsheets", "evidence": {}}
        v["control_key"] = key
        out.append(v)
    return out


def apply(decisions: list[dict], rules: dict, set_enabled=None) -> list[dict]:
    """Execute at most max_changes_per_run flag flips — ONLY if auto_execute.
    Returns the list of actions taken (empty when advising only)."""
    if not rules.get("auto_execute"):
        return []
    if set_enabled is None:
        import controls
        set_enabled = controls.set_enabled
    actions, budget = [], int(rules.get("max_changes_per_run", 1))
    for d in decisions:
        if budget <= 0:
            break
        key = d.get("control_key")
        if not key:
            continue
        if d["verdict"] == "RETIRE" and d["enabled"]:
            set_enabled(key, False)
            actions.append({"book": d["book"], "action": "disabled",
                            "reason": d["reason"]})
            budget -= 1
        elif d["verdict"] == "PROMOTE" and not d["enabled"]:
            set_enabled(key, True)
            actions.append({"book": d["book"], "action": "enabled",
                            "reason": d["reason"]})
            budget -= 1
    return actions


def main():
    rules = load_rules()
    try:
        strategies = json.loads((RESULTS / "tearsheets.json").read_text())["strategies"]
    except Exception as e:
        print(f"  Promotion advisor: no tearsheets to evaluate ({e}) — skipped.")
        return
    try:
        import controls
        flags = controls.flags()
    except Exception:
        flags = {}

    decisions = evaluate(strategies, rules, flags)
    actions = apply(decisions, rules)

    advice = {"generated": date.today().isoformat(),
              "auto_execute": rules["auto_execute"],
              "decisions": decisions, "actions": actions}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "promotion_advice.json").write_text(json.dumps(advice, indent=2))

    # Append-only audit trail of every run that recommended or acted.
    log_path = RESULTS / "promotion_log.json"
    try:
        log = json.loads(log_path.read_text())
    except Exception:
        log = []
    log.append({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "actions": actions,
                "verdicts": {d["book"]: d["verdict"] for d in decisions}})
    log_path.write_text(json.dumps(log[-400:], indent=2))

    mode = "AUTO" if rules["auto_execute"] else "advise-only"
    print(f"  Promotion advisor ({mode}):")
    for d in decisions:
        print(f"    {d['book']:<10} {d['verdict']:<8} {d['reason']}")
    for a in actions:
        print(f"    → EXECUTED: {a['book']} {a['action']} ({a['reason']})")


if __name__ == "__main__":
    main()
