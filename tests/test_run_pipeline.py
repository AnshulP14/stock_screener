"""screener.markets.run_pipeline -- the shared orchestration engine
replacing markets/nse.py's and markets/us.py's previously separate run()
implementations.

While merging them, found that the original nse.py's `if mode ==
"sync-universe": ... elif ...: ... else: <fetch+save logic>` structure put
the actual fetch+save code only in the `else` branch. sync-universe fell
through the whole if/elif/else with no return whenever there were
stale/missing symbols, implicitly returning None instead of ever fetching --
reproduced with a standalone repro (not shown here) before this fix. These
tests pin that sync-universe now actually reaches the fetch path.
"""

import pytest

from screener import index as index_mod
from screener.market import MarketConfig
from screener.markets import run_pipeline


@pytest.fixture(autouse=True)
def _isolate_manifest(tmp_path, monkeypatch):
    """update_manifest reads/writes index.MANIFEST_PATH directly (a module-level
    constant, not a parameter) -- without this, every test below writes a bogus
    entry into the real data/manifest.json (confirmed: this happened during
    manual testing before this fixture existed)."""
    monkeypatch.setattr(index_mod, "MANIFEST_PATH", tmp_path / "manifest.json")


def _test_market(tmp_path, *, fetch_universe, valid_modes=("sync-universe",)) -> MarketConfig:
    return MarketConfig(
        id="test",
        label="TEST",
        currency="USD",
        ticker_suffix="",
        fiscal_year=lambda d: d.year,
        companies_dir=tmp_path / "companies",
        indices_dir=tmp_path / "indices",
        failed_tickers_path=tmp_path / "failed.txt",
        valid_modes=valid_modes,
        fetch_universe=fetch_universe,
        staleness_policies=lambda days_old: (),  # every symbol in `symbols` counts as stale
    )


def test_sync_universe_reaches_dry_run_when_symbols_are_stale(tmp_path):
    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA", "BBB"], None))
    result = run_pipeline(market, mode="sync-universe", dry_run=True)
    # Before the fix this returned None (fell through the if/elif/else).
    assert result is not None
    assert result["fetched"] == 0 and result["failed"] == 0


def test_sync_universe_with_no_stale_symbols_still_returns_a_result(tmp_path):
    market = _test_market(tmp_path, fetch_universe=lambda: ([], None))
    result = run_pipeline(market, mode="sync-universe")
    assert result == {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": result["elapsed"]}


def test_invalid_mode_for_market_raises(tmp_path):
    market = _test_market(tmp_path, fetch_universe=lambda: ([], None), valid_modes=("full",))
    with pytest.raises(ValueError, match="not supported"):
        run_pipeline(market, mode="quick")


def test_incremental_always_rechecks_live_universe_even_with_existing_local_data(tmp_path):
    # User explicitly asked for both markets to auto-discover brand-new
    # listings on every incremental run (not just sync-universe), matching
    # the pre-unification snp.py behavior. Two symbols already have local
    # files, but the live universe (from fetch_universe) has a third, never
    # fetched -- it must show up as stale even though companies_dir is
    # non-empty, proving incremental doesn't just glob the local directory.
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text("{}")
    (companies_dir / "BBB.json").write_text("{}")

    calls = []

    def fetch_universe():
        calls.append(1)
        return ["AAA", "BBB", "CCC"], None

    market = _test_market(tmp_path, fetch_universe=fetch_universe, valid_modes=("incremental",))
    result = run_pipeline(market, mode="incremental", dry_run=True)

    assert calls, "fetch_universe was never called -- incremental fell back to globbing local files"
    assert result is not None


def test_skipped_count_reflects_full_universe_not_hardcoded_zero(tmp_path):
    # A code review caught that _finish() always returned skipped=0 --
    # matching NSE's original (arguably-accidental) behavior in these
    # early-return paths, but a real regression for the pre-unification
    # us.py, which reported skipped=len(universe) here. Two companies exist
    # on disk and empty staleness_policies means neither is stale, so nothing
    # gets fetched -- skipped should reflect the full universe size (2), not 0.
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text("{}")
    (companies_dir / "BBB.json").write_text("{}")
    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA", "BBB"], None))

    result = run_pipeline(market, mode="sync-universe")

    assert result["skipped"] == 2
