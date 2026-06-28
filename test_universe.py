"""
test_universe.py — tests for the config-driven Universe Manager + back-compat.
"""

import universe
from universe import UniverseManager, UniverseError


def test_named_universes_listed():
    um = UniverseManager()
    names = um.list()
    for n in ("NIFTY50", "NIFTY_NEXT_50", "NIFTY100", "NIFTY200", "fno"):
        assert n in names


def test_nifty50_members_and_no_hardcoded_in_code():
    um = UniverseManager()
    m = um.members("NIFTY50")
    assert len(m) == 50 and "RELIANCE" in m
    # membership comes from config (data file), not a Python literal
    import fetch_data
    assert fetch_data._NIFTY50 == m                  # fetch_data sources it from here


def test_compose_unions_and_dedupe():
    um = UniverseManager()
    # Next-50 is an empty slot, so NIFTY100/200 currently == NIFTY50 (composed, deduped)
    assert um.members("NIFTY100") == um.members("NIFTY50")
    assert um.members("NIFTY200") == um.members("NIFTY50")
    assert len(um.members("NIFTY200")) == len(set(um.members("NIFTY200")))   # no dupes


def test_resolve_intersects_available_data():
    um = UniverseManager()
    full = set(um.members("NIFTY50"))
    usable = set(um.resolve("NIFTY50"))
    assert usable <= full and usable <= um.available_symbols()
    cov = um.coverage("NIFTY50")
    assert cov["configured"] == 50
    assert cov["available"] == len(usable)
    assert "LTIM" in cov["missing"] and "TATAMOTORS" in cov["missing"]   # no data


def test_sectors_and_sector_of():
    um = UniverseManager()
    assert "Financials" in um.sectors()
    fin = um.sector("Financials")                    # resolved against data
    assert all(s in um.available_symbols() for s in fin)
    assert um.sector_of("HDFCBANK") == "Financials"
    assert um.sector_of("NOT_A_SYMBOL") == "Other"


def test_custom_universe_validated():
    um = UniverseManager()
    assert um.custom(["RELIANCE", "ITC", "NOPE_XYZ"]) == ["RELIANCE", "ITC"]
    named = um.custom("example_watchlist")
    assert "RELIANCE" in named


def test_unknown_raises():
    um = UniverseManager()
    for call in (lambda: um.members("NIFTY9000"),
                 lambda: um.sector("Nope"),
                 lambda: um.custom("missing_list")):
        try:
            call(); assert False, "expected UniverseError"
        except UniverseError:
            pass


def test_backward_compat_sector_map():
    # portfolio_analyzer re-exports the same symbol->sector map from config.
    import portfolio_analyzer
    assert portfolio_analyzer.SECTOR_MAP == UniverseManager().SECTOR_MAP
    assert portfolio_analyzer.SECTOR_MAP["TCS"] == "IT"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
