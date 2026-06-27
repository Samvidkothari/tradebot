"""
data_version.py — content-addressed versioning of the cached market data.

Builds a manifest of {symbol: {last_date, n_rows, sha1}} over data/*.csv and a
short `version` hash of it. The version changes IFF the data changes — so it can:
  • key the feature cache (auto-invalidate computed features when data changes);
  • detect which symbols changed between two snapshots (incremental updates);
  • give research a reproducible data-lineage stamp.

The manifest is written to data/_manifest.json. READ-ONLY w.r.t. market data; no
orders.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

import data_io

MANIFEST_PATH = data_io.DATA_DIR / "_manifest.json"


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    h.update(path.read_bytes())
    return h.hexdigest()


class DataManifest:
    """Snapshot of the data directory's content + a version hash."""

    def __init__(self, data_dir: Path = data_io.DATA_DIR):
        self.data_dir = Path(data_dir)
        self._entries: dict | None = None

    def build(self) -> dict:
        """{symbol: {last_date, n_rows, sha1}} for every CSV (index included)."""
        entries = {}
        for fp in sorted(self.data_dir.glob("*.csv")):
            try:
                df = pd.read_csv(fp, usecols=["date"])
                last = str(pd.to_datetime(df["date"]).max().date())
                n = int(len(df))
            except Exception:
                last, n = None, 0
            entries[fp.stem] = {"last_date": last, "n_rows": n, "sha1": _sha1(fp)}
        self._entries = entries
        return entries

    @property
    def entries(self) -> dict:
        if self._entries is None:
            self.build()
        return self._entries

    @property
    def version(self) -> str:
        """Short hash that changes iff any file's content changes."""
        blob = json.dumps(self.entries, sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()[:12]

    def write(self, path: Path = MANIFEST_PATH):
        payload = {"version": self.version, "symbols": self.entries}
        Path(path).write_text(json.dumps(payload, indent=2))
        return self.version

    @staticmethod
    def read(path: Path = MANIFEST_PATH) -> dict | None:
        p = Path(path)
        return json.loads(p.read_text()) if p.exists() else None

    def changed_symbols(self, previous: dict | None) -> list[str]:
        """Symbols whose content differs from a previous manifest dict."""
        prev = (previous or {}).get("symbols", {})
        out = []
        for sym, info in self.entries.items():
            if prev.get(sym, {}).get("sha1") != info["sha1"]:
                out.append(sym)
        return sorted(out)
