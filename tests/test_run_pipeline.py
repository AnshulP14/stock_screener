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

import json

import pandas as pd
import pytest

from screener import index as index_mod
from screener import markets as markets_mod
from screener.market import MarketConfig
from screener.markets import run_pipeline


@pytest.fixture(autouse=True)
def _isolate_manifest(tmp_path, monkeypatch):
    """update_manifest reads/writes index.MANIFEST_PATH directly (a module-level
    constant, not a parameter) -- without this, every test below writes a bogus
    entry into the real data/manifest.json (confirmed: this happened during
    manual testing before this fixture existed)."""
    monkeypatch.setattr(index_mod, "MANIFEST_PATH", tmp_path / "manifest.json")


def _test_market(tmp_path, *, fetch_universe, valid_modes=("sync-universe",), **kwargs) -> MarketConfig:
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
        **kwargs,
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


# ── uses_edgar / fetch_institutional_holders routing ─────────────────

def test_full_mode_with_uses_edgar_routes_through_edgar_trends_and_cik(tmp_path, monkeypatch):
    """A market with uses_edgar=True must build the CIK map once, resolve
    each symbol's CIK from it, fetch EDGAR facts, and write historical_trends
    from build_historical_trends_edgar (not the yfinance-based
    build_historical_trends) -- with cik carried into the company JSON."""
    cik_map_calls = []
    edgar_calls = []

    def fake_build_cik_map():
        cik_map_calls.append(1)
        return {"AAA": 111}

    def fake_fetch_edgar_facts(symbol, cik):
        edgar_calls.append((symbol, cik))
        return {"facts": {"us-gaap": {}}}  # no tags -> no years, but proves the route was taken

    def fake_fetch_ticker_data(symbol, *, institutional_holders=False):
        return {"symbol": symbol, "info": {}, "fetch_time": "2026-01-01", "error": None}

    monkeypatch.setattr(markets_mod, "build_cik_map", fake_build_cik_map)
    monkeypatch.setattr(markets_mod, "fetch_edgar_facts", fake_fetch_edgar_facts)
    monkeypatch.setattr(markets_mod, "fetch_ticker_data", fake_fetch_ticker_data)

    market = _test_market(
        tmp_path, fetch_universe=lambda: (["AAA"], None), valid_modes=("full",), uses_edgar=True,
    )
    run_pipeline(market, mode="full")

    assert cik_map_calls == [1]  # built once for the whole run, not per symbol
    assert edgar_calls == [("AAA", 111)]

    company = json.loads((tmp_path / "companies" / "AAA.json").read_text())
    assert company["cik"] == 111
    assert company["historical_trends"]["source"] == "edgar_xbrl"


def test_full_mode_without_uses_edgar_does_not_build_cik_map(tmp_path, monkeypatch):
    cik_map_calls = []
    monkeypatch.setattr(markets_mod, "build_cik_map", lambda: cik_map_calls.append(1))
    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {"symbol": symbol, "info": {}, "fetch_time": "2026-01-01", "error": None},
    )

    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA"], None), valid_modes=("full",))
    run_pipeline(market, mode="full")

    assert cik_map_calls == []
    company = json.loads((tmp_path / "companies" / "AAA.json").read_text())
    assert company["cik"] is None
    assert company["historical_trends"].get("source") != "edgar_xbrl"


def test_fetch_institutional_holders_flag_populates_institutional_ownership(tmp_path, monkeypatch):
    holders_df = pd.DataFrame([{"Holder": "Blackrock Inc.", "Shares": 100.0, "pctHeld": 0.05}])

    def fake_fetch_ticker_data(symbol, *, institutional_holders=False):
        assert institutional_holders is True
        return {
            "symbol": symbol,
            "info": {"heldPercentInsiders": 0.01, "heldPercentInstitutions": 0.5},
            "institutional_holders": holders_df,
            "annual_income": pd.DataFrame(),
            "annual_balance": pd.DataFrame(),
            "annual_cashflow": pd.DataFrame(),
            "fetch_time": "2026-01-01",
            "error": None,
        }

    monkeypatch.setattr(markets_mod, "fetch_ticker_data", fake_fetch_ticker_data)

    market = _test_market(
        tmp_path, fetch_universe=lambda: (["AAA"], None), valid_modes=("full",),
        fetch_institutional_holders=True,
    )
    run_pipeline(market, mode="full")

    company = json.loads((tmp_path / "companies" / "AAA.json").read_text())
    io = company["institutional_ownership"]
    assert io["pct_insider"] == pytest.approx(1.0)
    assert io["top_holders"] == [{"holder": "Blackrock Inc.", "shares": 100.0, "pct_out": pytest.approx(5.0)}]
