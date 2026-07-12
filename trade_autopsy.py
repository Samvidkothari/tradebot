"""trade_autopsy.py — standardized post-trade autopsy packet (Pillar 5).

Turns a tagged trade (trade_journal.TradeRecord / dict) into a consistent markdown
"autopsy" — execution details, slip vs cost, rule-adherence, and chart coordinates
— plus a ready-to-send LLM coaching prompt. This is the structured input a human
or an LLM coach reviews; it standardizes what `llm_analyst.py` / `review.py`
currently do ad hoc, so every trade is post-mortem'd the same way.

Pure formatting — no I/O, no orders, no network. Feed the prompt to any model.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass


def _d(trade) -> dict:
    return asdict(trade) if is_dataclass(trade) else dict(trade)


def _fnum(x, pct=False):
    if x is None:
        return "—"
    try:
        return f"{x*100:+.2f}%" if pct else f"{x:,.2f}"
    except (TypeError, ValueError):
        return str(x)


def autopsy_markdown(trade, slippage: float | None = None) -> str:
    """A standardized markdown autopsy for one trade. `slippage` (optional) is the
    modeled fill slip as a return fraction, shown against booked cost."""
    t = _d(trade)
    side = t.get("side", "long")
    entry, exit_ = t.get("entry"), t.get("exit")
    stop = t.get("stop", t.get("sl"))
    R = t.get("R")
    won = (R is not None and R > 0)
    target_hint = t.get("target", "—")

    lines = [
        f"## Trade Autopsy — {t.get('setup_type','?')} · {t.get('symbol','?')} · {side.upper()}",
        f"*{t.get('entry_date','?')} → {t.get('exit_date','?')}  ·  "
        f"regime: {t.get('regime') or 'n/a'}  ·  {t.get('dow','?')}  ·  "
        f"held {t.get('hold_bars','?')}d*",
        "",
        "### Execution",
        f"- **Entry / Exit / Stop:** {_fnum(entry)} / {_fnum(exit_)} / {_fnum(stop)}",
        f"- **Side / exit reason:** {side} / {t.get('exit_reason','?')}",
        f"- **Result:** {'WIN' if won else 'LOSS'} · **{_fnum(R)+'R' if R is not None else '—'}** "
        f"· net {_fnum(t.get('net_ret'), pct=True)} (gross {_fnum(t.get('gross_ret'), pct=True)})",
        "",
        "### Cost & slippage",
        f"- **Round-trip cost:** {_fnum(t.get('cost'), pct=True)}"
        + (f"  ·  **modeled slippage:** {_fnum(slippage, pct=True)}" if slippage is not None else ""),
        f"- **Cost drag on this trade:** {_fnum((t.get('gross_ret') or 0) - (t.get('net_ret') or 0), pct=True)}",
        "",
        "### Rule adherence",
        f"- **Followed pre-defined rules:** {'✅ yes' if t.get('rule_adherence', True) else '❌ NO — process error'}",
        f"- **Valid loss vs bad process:** "
        + ("valid outcome (rules followed)" if t.get("rule_adherence", True)
           else "BAD PROCESS — review before repeating"),
        "",
        "### Chart coordinates (for annotation)",
        f"- entry@{_fnum(entry)} ({t.get('entry_date','?')}), "
        f"exit@{_fnum(exit_)} ({t.get('exit_date','?')}), stop@{_fnum(stop)}, target {target_hint}",
    ]
    if t.get("note"):
        lines += ["", f"> {t['note']}"]
    return "\n".join(lines)


COACH_PROMPT_HEADER = (
    "You are a trading-process coach. Review the trade autopsies below. Focus ONLY "
    "on PROCESS and RISK, never on prediction. For each, judge: was this a valid "
    "outcome (rules followed) or a process error? Was sizing/exit disciplined? Then "
    "give the single highest-leverage process fix across all the trades. Be concrete "
    "and unsentimental.\n"
)


def coaching_prompt(trades, context: str = "") -> str:
    """Bundle N autopsies into one LLM coaching prompt."""
    body = "\n\n---\n\n".join(autopsy_markdown(t) for t in trades)
    ctx = f"\nContext: {context}\n" if context else ""
    return f"{COACH_PROMPT_HEADER}{ctx}\n{body}\n\n---\nNow give your process review."


def session_autopsy(trades, title: str = "Session") -> str:
    """A batch autopsy document (header stats + each trade) for the review folder."""
    tl = [_d(t) for t in trades]
    n = len(tl)
    Rs = [t.get("R") for t in tl if t.get("R") is not None]
    wins = sum(1 for r in Rs if r > 0)
    avg = (sum(Rs) / len(Rs)) if Rs else 0.0
    head = [f"# {title} — {n} trades",
            f"\nWin rate {wins}/{len(Rs) if Rs else 0}"
            f" · avg {avg:+.2f}R"
            f" · process errors {sum(1 for t in tl if not t.get('rule_adherence', True))}\n"]
    return "\n".join(head) + "\n\n---\n\n".join(autopsy_markdown(t) for t in trades)
