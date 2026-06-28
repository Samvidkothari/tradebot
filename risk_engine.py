"""
risk_engine.py — risk MONITOR for the low-vol paper book (downside protection).

Evaluates the live equity book against configured limits (risk_limits.json) and
reports per-check status + an aggregate Emergency flag:

  • ATR position sizing     — vol-based size per holding (a sizing utility)
  • Daily loss limit        — latest daily return vs limit
  • Max drawdown            — current drawdown vs limit
  • Sector exposure         — largest sector weight vs limit
  • Correlation monitoring  — average pairwise correlation vs limit
  • Emergency stop          — aggregate: raised when a HARD limit (daily loss or
                              max drawdown) breaches

CRITICAL — MONITORING ONLY. Nothing here places, modifies, or stops an order;
there is no live trading to halt. The "Emergency stop" is a status flag (what a
human would act on in a hypothetical semi-auto live system), NOT an automatic
kill switch. Reuses portfolio_analyzer / risk_analytics maths.

Usage:  python risk_engine.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from config import PAPER_CAPITAL

CONFIG_PATH = Path(__file__).parent / "risk_limits.json"
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class RiskLimits:
    daily_loss_limit: float = -0.03
    max_drawdown_limit: float = -0.20
    sector_limit: float = 0.35
    correlation_limit: float = 0.50
    atr_risk_pct: float = 0.01
    atr_multiplier: float = 2.0

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH):
        cfg = {k: v for k, v in json.loads(Path(path).read_text()).items()
               if not k.startswith("_")}
        return cls(**cfg)


def _check(value, limit, hard: bool, breach_when_below: bool = True) -> dict:
    """One limit check. breach_when_below: value <= limit is a breach (losses/DD);
    otherwise value >= limit is a breach (exposure/correlation)."""
    if value is None:
        return {"value": None, "limit": limit, "status": "n/a", "hard": hard}
    breached = (value <= limit) if breach_when_below else (value >= limit)
    return {"value": round(float(value), 4), "limit": limit,
            "status": "BREACH" if breached else "OK", "hard": hard}


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def evaluate(self) -> dict:
        from data_layer import MarketDataManager
        from strategy_base import REGISTRY, MonthlyRebalanceEngine
        import portfolio_analyzer as PA
        import risk_analytics as RA

        L = self.limits
        mgr = MarketDataManager()
        panel = mgr.close_panel()

        # equity curve of the low-vol book (research engine, same as the tear sheet)
        equity, _, _ = MonthlyRebalanceEngine().run(REGISTRY["lowvol"], panel)
        dd = RA.drawdown_stats(equity)
        daily_ret = float(equity.pct_change().iloc[-1]) if len(equity) > 1 else None

        # holdings / sector / correlation (reuse the portfolio analyzer)
        pa = PA.analyze()
        if "error" in pa:
            sector_max, avg_corr, holdings = None, None, []
        else:
            sector_max = max(pa["sectors"].values()) if pa["sectors"] else None
            avg_corr = pa["avg_pairwise_corr"]
            holdings = pa["holdings"]

        checks = {
            "daily_loss":   _check(daily_ret, L.daily_loss_limit, hard=True),
            "max_drawdown": _check(dd["current_drawdown"], L.max_drawdown_limit, hard=True),
            "sector_exposure": _check(sector_max, L.sector_limit, hard=False,
                                      breach_when_below=False),
            "correlation":  _check(avg_corr, L.correlation_limit, hard=False,
                                   breach_when_below=False),
        }

        # ATR position sizing for the held names (vol-based sizing utility)
        atr_sizing = []
        for h in holdings[:12]:
            sym = h["symbol"]
            df = mgr.ohlcv(sym)
            if df is None:
                continue
            a = RA.atr(df["high"], df["low"], df["close"])
            size = RA.position_size_atr(PAPER_CAPITAL, L.atr_risk_pct, a, L.atr_multiplier)
            atr_sizing.append({"symbol": sym, "atr": round(a, 1) if a else None,
                               "units": size["units"]})

        hard_breach = [k for k, c in checks.items() if c["hard"] and c["status"] == "BREACH"]
        soft_breach = [k for k, c in checks.items() if not c["hard"] and c["status"] == "BREACH"]
        emergency = bool(hard_breach)
        status = "EMERGENCY" if emergency else "WARN" if soft_breach else "OK"
        reason = (f"hard limit breached: {', '.join(hard_breach)}" if hard_breach
                  else f"soft limit breached: {', '.join(soft_breach)}" if soft_breach
                  else "all limits within bounds")

        return {
            "generated": date.today().isoformat(),
            "as_of": mgr.as_of(),
            "status": status, "emergency": emergency, "reason": reason,
            "checks": checks, "atr_sizing": atr_sizing,
            "note": "Monitoring only — flags breaches; places/halts no orders.",
        }


def main():
    import schemas
    RESULTS_DIR.mkdir(exist_ok=True)
    report = RiskEngine(RiskLimits.from_config()).evaluate()
    (RESULTS_DIR / "risk_engine.json").write_text(
        json.dumps(schemas.validate("risk_engine.json", report), indent=2))
    c = report["checks"]
    print(f"  Risk engine: {report['status']} — {report['reason']}")
    for name, chk in c.items():
        print(f"    {name:<16} {chk['status']:<7} value={chk['value']} limit={chk['limit']}")
    print(f"  → results/risk_engine.json")


if __name__ == "__main__":
    main()
