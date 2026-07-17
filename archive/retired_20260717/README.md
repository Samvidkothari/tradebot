# Retired 2026-07-17 (cleanup pass)

Moved here, not deleted — restore with `git mv` / `mv` if needed.

- watch.py    — Kite-era manual holdings viewer; zero imports/refs from any
                entry point, script, view, or test.
- exchange.py — Kite request_token swap helper; superseded by login.py.
- review.py   — read-only portfolio.db summary; superseded by the dashboard
                (views_simple/views_research). Zero refs.
- _backup_pre_redesign/ — pre-redesign snapshot from 2026-06-16; was
                git-ignored (unrecoverable if deleted), so parked here.

Deleted outright in the same pass (untracked junk): .DS_Store, .fuse_hidden*,
__wt_*.junk.* stray sqlite artifacts, llm.db.demo.3697, __pycache__,
.pytest_cache, .ruff_cache, old jobs/*.log|*.rc run logs.
