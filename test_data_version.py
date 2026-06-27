"""
test_data_version.py — tests for content-addressed data versioning.
"""

import copy

from data_version import DataManifest


def test_manifest_entries_shape():
    m = DataManifest()
    e = m.build()
    assert len(e) > 0
    for sym, info in e.items():
        assert set(info) == {"last_date", "n_rows", "sha1"}
        assert len(info["sha1"]) == 40            # sha1 hex


def test_version_is_stable_and_short():
    v1 = DataManifest().version
    v2 = DataManifest().version
    assert v1 == v2                               # same data → same version
    assert isinstance(v1, str) and len(v1) == 12


def test_changed_symbols_detects_diff():
    m = DataManifest()
    cur = {"version": m.version, "symbols": m.entries}
    assert m.changed_symbols(cur) == []           # vs itself → nothing changed
    # Mutate a previous snapshot → that symbol shows as changed.
    prev = copy.deepcopy(cur)
    some = next(iter(prev["symbols"]))
    prev["symbols"][some]["sha1"] = "0" * 40
    assert some in m.changed_symbols(prev)


def test_changed_symbols_vs_none_is_all():
    m = DataManifest()
    assert set(m.changed_symbols(None)) == set(m.entries)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
