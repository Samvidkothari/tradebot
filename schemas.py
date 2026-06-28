"""
schemas.py — typed contracts for the results/*.json research payloads.

Single source of truth for the runner → JSON → dashboard/template boundary. The
`TypedDict`s document each payload's shape (self-documenting; zero runtime cost),
and `REQUIRED` + `validate()` enforce the top-level keys the dashboard/templates
actually depend on — so a runner that drops or renames a key fails LOUDLY at
generation time instead of silently breaking a dashboard page.

Used by:
  • each research runner — `schemas.validate("<file>.json", payload)` before write;
  • `test_integration.py` — checks produced JSON against `REQUIRED`.

Pure declarations + a tiny validator. No I/O, no orders.
"""

from __future__ import annotations

from typing import Any, TypedDict


# ── Payload shapes (documentation — TypedDict is a plain dict at runtime) ──────

class TearsheetsJSON(TypedDict):
    generated: str
    regime: dict           # regime.classify() output
    strategies: dict       # name -> per-strategy tear sheet


class FactorsJSON(TypedDict):
    generated: str
    as_of: str
    unavailable: list      # fundamental factors we cannot compute
    weights: dict          # composite weights
    factors: dict          # name -> leaderboard
    composite: list        # ranked composite


class PortfolioJSON(TypedDict):
    generated: str
    as_of: str
    n_holdings: int
    avg_pairwise_corr: float
    concentration: dict
    actual_equal_weight: dict
    inverse_vol: dict
    holdings: list
    sectors: dict


class RiskJSON(TypedDict):
    generated: str
    strategies: dict
    atr_sizing: list


class AttributionJSON(TypedDict):
    generated: str
    strategies: dict


class DataQualityJSON(TypedDict):
    generated: str
    panel_start: str
    panel_end: str
    n_symbols: int
    cache_stale_days: int
    summary: dict
    symbols: list


# ── Enforced contract (top-level keys the dashboard/templates require) ────────

REQUIRED: dict[str, list[str]] = {
    "tearsheets.json":   ["generated", "regime", "strategies"],
    "factors.json":      ["generated", "as_of", "unavailable", "factors", "composite"],
    "portfolio.json":    ["as_of", "n_holdings", "avg_pairwise_corr", "concentration",
                          "actual_equal_weight", "inverse_vol", "holdings", "sectors"],
    "risk.json":         ["generated", "strategies"],
    "attribution.json":  ["generated", "strategies"],
    "data_quality.json": ["panel_start", "panel_end", "n_symbols",
                          "cache_stale_days", "summary", "symbols"],
    "feature_store.json": ["as_of", "data_version", "n_features",
                           "materialize", "features"],
    "multifactor.json": ["as_of", "model", "weights", "top"],
    "optimizer.json": ["candidates", "scheme", "weights", "cash", "diagnostics"],
    "risk_engine.json": ["status", "emergency", "reason", "checks", "atr_sizing"],
    "market_intel.json": ["expiries", "sectors", "surveillance", "circuits",
                          "corporate_actions", "n_symbols"],
    "research_assistant.json": ["generated", "summary", "findings"],
}


class SchemaError(ValueError):
    """Raised when a payload is missing keys its consumers require."""


def validate(name: str, data: Any) -> Any:
    """Validate `data` (the to-be-written payload for results/<name>) against the
    enforced contract. Returns `data` unchanged on success; raises SchemaError
    listing the missing keys otherwise."""
    if name not in REQUIRED:
        raise SchemaError(f"no schema registered for '{name}'")
    if not isinstance(data, dict):
        raise SchemaError(f"{name}: payload must be a dict, got {type(data).__name__}")
    missing = [k for k in REQUIRED[name] if k not in data]
    if missing:
        raise SchemaError(f"{name}: missing required key(s): {', '.join(missing)}")
    return data
