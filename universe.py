"""
universe.py — modular, configuration-driven Universe Manager.

Universe membership lives in universes.json (config DATA, not code) — there are no
hardcoded stock lists in Python. Supports named indices (NIFTY50, NIFTY Next 50,
NIFTY100, NIFTY200 — composable via unions), F&O, sector-wise, and custom universes.

Two deliberately-separate views:
  • members(name) — the FULL configured membership (may include names we hold no
                    price data for; indices/F&O we lack data for are empty CONFIG
                    SLOTS to be filled from an official NSE source).
  • resolve(name) — members ∩ symbols actually available in data/. THIS is what
                    research should use, so it never gets a symbol it can't load.

READ-ONLY; no orders. Membership lists are point-in-time (survivorship caveat).
"""

from __future__ import annotations

import json
from pathlib import Path

import data_io

CONFIG_PATH = Path(__file__).parent / "universes.json"


class UniverseError(KeyError):
    """Unknown universe / sector name, or a cyclic compose."""


class UniverseManager:
    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config = json.loads(Path(config_path).read_text())

    # ── named index / F&O universes ───────────────────────────────────────────

    def list(self) -> list[str]:
        """All selectable index/F&O universe names."""
        names = list(self.config.get("indices", {}))
        if "fno" in self.config:
            names.append("fno")
        return names

    def _members(self, name: str, seen: set | None = None) -> list[str]:
        indices = self.config.get("indices", {})
        if name == "fno":
            spec = self.config.get("fno", [])
        elif name in indices:
            spec = indices[name]
        else:
            raise UniverseError(f"unknown universe '{name}' (have: {self.list()})")
        if isinstance(spec, dict) and "compose" in spec:        # union of sub-universes
            seen = seen or set()
            if name in seen:
                raise UniverseError(f"cyclic compose involving '{name}'")
            seen.add(name)
            out: list[str] = []
            for sub in spec["compose"]:
                out += self._members(sub, seen)
            return list(dict.fromkeys(out))                     # dedupe, keep order
        return list(spec)

    def members(self, name: str) -> list[str]:
        """Full configured membership (composition expanded)."""
        return self._members(name)

    def available_symbols(self) -> set[str]:
        """Symbols we actually have price data for."""
        return set(data_io.close_panel().columns)

    def resolve(self, name: str) -> list[str]:
        """members(name) ∩ available data — the usable universe for research."""
        avail = self.available_symbols()
        return [s for s in self.members(name) if s in avail]

    def fno(self) -> list[str]:
        return self.resolve("fno")

    def coverage(self, name: str) -> dict:
        """How much of a universe we can actually load."""
        full, usable = self.members(name), self.resolve(name)
        return {"universe": name, "configured": len(full), "available": len(usable),
                "missing": sorted(set(full) - set(usable))}

    # ── sector-wise universes ─────────────────────────────────────────────────

    def sectors(self) -> list[str]:
        return sorted(self.config.get("sectors", {}))

    def sector(self, sector: str, resolve: bool = True) -> list[str]:
        members = self.config.get("sectors", {}).get(sector)
        if members is None:
            raise UniverseError(f"unknown sector '{sector}' (have: {self.sectors()})")
        if resolve:
            avail = self.available_symbols()
            return [s for s in members if s in avail]
        return list(members)

    @property
    def SECTOR_MAP(self) -> dict[str, str]:
        """symbol -> sector (back-compat with the old portfolio_analyzer.SECTOR_MAP)."""
        out: dict[str, str] = {}
        for sec, syms in self.config.get("sectors", {}).items():
            for s in syms:
                out[s] = sec
        return out

    def sector_of(self, symbol: str) -> str:
        return self.SECTOR_MAP.get(symbol, "Other")

    # ── custom universes ──────────────────────────────────────────────────────

    def custom(self, symbols, resolve: bool = True) -> list[str]:
        """An ad-hoc universe from an explicit symbol list (or a named one from
        config['custom'])."""
        if isinstance(symbols, str):
            named = self.config.get("custom", {}).get(symbols)
            if named is None:
                raise UniverseError(f"unknown custom universe '{symbols}'")
            symbols = named
        if resolve:
            avail = self.available_symbols()
            return [s for s in symbols if s in avail]
        return list(symbols)


# Module-level convenience instance.
DEFAULT = UniverseManager()
