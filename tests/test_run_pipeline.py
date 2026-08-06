"""Tests for shared market-pipeline orchestration."""

import json

import pandas as pd
import pytest

from screener import index as index_mod
from screener import pipeline as markets_mod
from screener.market import MarketConfig
from screener.pipeline import run_pipeline


@pytest.fixture(autouse=True)
def _isolate_external_state(tmp_path, monkeypatch):
    """Keep orchestration tests off the network and the real data directory."""
    monkeypatch.setattr(index_mod, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(markets_mod, "rebuild_db", lambda market: {})
    monkeypatch.setattr(markets_mod, "process_symbol_full", lambda *args, **kwargs: [])
    monkeypatch.setattr(markets_mod, "is_report_stale", lambda *args, **kwargs: False)
    monkeypatch.setattr(markets_mod, "screener_session", object)
    monkeypatch.setattr(markets_mod, "parse_nse_bank_history", lambda symbol: {})
    monkeypatch.setattr(markets_mod, "parse_ffiec_history", lambda rssd: {})
    monkeypatch.setattr("screener.runner.AdaptiveRateLimiter.acquire", lambda self: None)


def _test_market(tmp_path, *, fetch_universe, **kwargs) -> MarketConfig:
    return MarketConfig(
        id=kwargs.pop("id", "test"),
        label="TEST",
        currency="USD",
        ticker_suffix="",
        fiscal_year=lambda d: d.year,
        companies_dir=tmp_path / "companies",
        indices_dir=tmp_path / "indices",
        failed_tickers_path=tmp_path / "failed.txt",
        fetch_universe=fetch_universe,
        staleness_policies=lambda days_old: (),  # missing files are stale; existing files are not
        **kwargs,
    )


def test_sync_universe_reaches_fetch_path_when_symbols_are_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {"symbol": symbol, "info": {}, "fetch_time": "2026-01-01", "error": None},
    )
    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA", "BBB"], None))
    result = run_pipeline(market, mode="quick-sync")
    # Before the fix this returned None (fell through the if/elif/else).
    assert result is not None
    assert result["fetched"] == 2 and result["failed"] == 0


def test_sync_universe_with_no_stale_symbols_still_returns_a_result(tmp_path):
    market = _test_market(tmp_path, fetch_universe=lambda: ([], None))
    result = run_pipeline(market, mode="quick-sync")
    assert result == {"fetched": 0, "failed": 0, "skipped": 0, "elapsed": result["elapsed"]}


def test_invalid_mode_raises(tmp_path):
    market = _test_market(tmp_path, fetch_universe=lambda: ([], None))
    with pytest.raises(ValueError, match="not supported"):
        run_pipeline(market, mode="invalid")


def test_incremental_always_rechecks_live_universe_even_with_existing_local_data(tmp_path, monkeypatch):
    # The live universe includes a new symbol absent from local files.
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text("{}")
    (companies_dir / "BBB.json").write_text("{}")

    calls = []

    def fetch_universe():
        calls.append(1)
        return ["AAA", "BBB", "CCC"], None

    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {"symbol": symbol, "info": {}, "fetch_time": "2026-01-01", "error": None},
    )
    market = _test_market(tmp_path, fetch_universe=fetch_universe)
    result = run_pipeline(market, mode="quick-sync")

    assert calls, "fetch_universe was never called -- incremental fell back to globbing local files"
    assert result is not None


def test_skipped_count_reflects_full_universe_not_hardcoded_zero(tmp_path):
    # Neither existing company is stale, so both count as skipped.
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text("{}")
    (companies_dir / "BBB.json").write_text("{}")
    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA", "BBB"], None))

    result = run_pipeline(market, mode="quick-sync")

    assert result["skipped"] == 2


def test_quick_sync_ranks_known_market_caps_before_unknowns(tmp_path):
    indices_dir = tmp_path / "indices"
    indices_dir.mkdir()
    (indices_dir / "screening_summary.json").write_text(json.dumps({"companies": [
        {"symbol": "SMALL", "market_cap": 10},
        {"symbol": "LARGE", "market_cap": 100},
    ]}))

    assert markets_mod._rank_by_mcap(indices_dir, {"SMALL", "LARGE", "UNKNOWN"}) == [
        "LARGE", "SMALL", "UNKNOWN",
    ]


def test_sync_removes_symbols_no_longer_in_the_live_universe(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "REMOVED.json").write_text("{}")
    market = _test_market(tmp_path, fetch_universe=lambda: ([], None))

    run_pipeline(market, mode="quick-sync")

    assert not (companies_dir / "REMOVED.json").exists()


# ── EDGAR routing ────────────────────────────────────────────────────

def test_full_mode_with_uses_edgar_routes_through_edgar_trends_and_cik(tmp_path, monkeypatch):
    """EDGAR markets resolve CIKs and build trends from companyfacts."""
    cik_map_calls = []
    edgar_calls = []

    def fake_build_cik_map():
        cik_map_calls.append(1)
        return {"AAA": 111}

    def fake_fetch_facts(symbol, cik):
        edgar_calls.append((symbol, cik))
        return {"facts": {"us-gaap": {}}}  # no tags -> no years, but proves the route was taken

    def fake_fetch_ticker_data(symbol, *, institutional_holders=False, annual_statements=True):
        return {"symbol": symbol, "info": {}, "fetch_time": "2026-01-01", "error": None}

    monkeypatch.setattr(markets_mod, "build_cik_map", fake_build_cik_map)
    monkeypatch.setattr(markets_mod, "fetch_facts", fake_fetch_facts)
    monkeypatch.setattr(markets_mod, "fetch_ticker_data", fake_fetch_ticker_data)

    market = _test_market(
        tmp_path, fetch_universe=lambda: (["AAA"], None), uses_edgar=True,
    )
    run_pipeline(market, mode="full-sync", fetch_reports=False)

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

    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA"], None))
    run_pipeline(market, mode="full-sync")

    assert cik_map_calls == []
    company = json.loads((tmp_path / "companies" / "AAA.json").read_text())
    assert company["cik"] is None
    assert company["historical_trends"].get("source") != "edgar_xbrl"


def test_edgar_market_populates_institutional_ownership(tmp_path, monkeypatch):
    holders_df = pd.DataFrame([{"Holder": "Blackrock Inc.", "Shares": 100.0, "pctHeld": 0.05}])

    def fake_fetch_ticker_data(symbol, *, institutional_holders=False, annual_statements=True):
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
    monkeypatch.setattr(markets_mod, "build_cik_map", lambda: {"AAA": 111})
    monkeypatch.setattr(markets_mod, "fetch_facts", lambda symbol, cik: {})

    market = _test_market(
        tmp_path, fetch_universe=lambda: (["AAA"], None), uses_edgar=True,
    )
    run_pipeline(market, mode="full-sync", fetch_reports=False)

    company = json.loads((tmp_path / "companies" / "AAA.json").read_text())
    io = company["institutional_ownership"]
    assert io["pct_insider"] == pytest.approx(1.0)
    assert io["top_holders"] == [{"holder": "Blackrock Inc.", "shares": 100.0, "pct_out": pytest.approx(5.0)}]


# ── manifest coverage (Phase 7 doc reconciliation) ────────────────────
# Coverage fields follow the market's configured enrichment sources.

def test_write_manifest_computes_enrichment_dataset_coverage(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text(json.dumps({"shareholding": {"promoter": [50.0]}}))
    (companies_dir / "BBB.json").write_text(json.dumps({"shareholding": None}))

    market = _test_market(
        tmp_path, fetch_universe=lambda: ([], None), enrichment_datasets=("shareholding",),
    )
    markets_mod._write_manifest(market, companies_dir)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["test"]["shareholding_coverage"] == 0.5


def test_write_manifest_edgar_coverage_needs_real_years_not_just_a_resolved_cik(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text(json.dumps(
        {"cik": 123, "historical_trends": {"fiscal_years": [2023, 2024]}}
    ))
    (companies_dir / "BBB.json").write_text(json.dumps(
        {"cik": 456, "historical_trends": {"years_available": []}}
    ))

    market = _test_market(tmp_path, fetch_universe=lambda: ([], None), uses_edgar=True)
    markets_mod._write_manifest(market, companies_dir)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["test"]["edgar_coverage"] == 0.5


def test_write_manifest_omits_coverage_keys_for_a_market_with_neither(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    (companies_dir / "AAA.json").write_text(json.dumps({}))

    market = _test_market(tmp_path, fetch_universe=lambda: ([], None))
    markets_mod._write_manifest(market, companies_dir)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "shareholding_coverage" not in manifest["test"]
    assert "edgar_coverage" not in manifest["test"]


# ── long-running job visibility ─────────────────────────────────────

def test_snp_report_job_logs_when_the_report_is_ready(tmp_path, monkeypatch, capsys):
    report_path = tmp_path / "10-k.htm"
    monkeypatch.setattr(
        markets_mod,
        "fetch_snp_reports",
        lambda *args, **kwargs: {"downloaded": [str(report_path)], "error": None},
    )
    indexed = []
    monkeypatch.setattr(markets_mod.hf, "build_index", indexed.append)

    assert markets_mod._download_and_index_snp("AAA", 123) is True
    assert indexed == [report_path]
    output = capsys.readouterr().out
    assert "[S&P 500 reports] AAA: downloading" in output
    assert "[S&P 500 reports] AAA: ready" in output


# ── Phase 3A acquisition routing ───────────────────────────────────

def test_non_bank_refresh_makes_no_regulatory_calls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {
            "symbol": symbol, "info": {"industry": "Software - Infrastructure"},
            "fetch_time": "2026-01-01", "error": None,
        },
    )
    monkeypatch.setattr(
        markets_mod, "download_nse_bank_filings", lambda *a, **kw: calls.append(a),
    )
    market = _test_market(tmp_path, id="nse", fetch_universe=lambda: (["AAA"], None))

    result = run_pipeline(market, mode="full-sync", fetch_reports=False)

    assert result["fetched"] == 1
    assert calls == []


def test_nse_bank_failure_keeps_base_fundamentals_and_skip_reports_does_not_skip_bank(
    tmp_path, monkeypatch, capsys,
):
    calls = []
    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {
            "symbol": symbol, "info": {"industry": "Banks - Regional"},
            "fetch_time": "2026-01-01", "error": None,
        },
    )

    def fail_bank(symbol, **kwargs):
        calls.append((symbol, kwargs["days_old"]))
        raise RuntimeError("NSE unavailable")

    monkeypatch.setattr(markets_mod, "download_nse_bank_filings", fail_bank)
    market = _test_market(
        tmp_path, id="nse", fetch_universe=lambda: (["HDFCBANK"], None),
    )

    result = run_pipeline(
        market, mode="full-sync", days_old=11, fetch_reports=False,
    )

    assert result["fetched"] == 1 and result["failed"] == 0
    assert calls == [("HDFCBANK", 11)]
    assert (tmp_path / "companies" / "HDFCBANK.json").exists()
    assert "Bank downloads: 1 errors" in capsys.readouterr().out


def test_snp_bank_downloads_years_once_and_stores_reviewed_rssd(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(markets_mod, "build_cik_map", lambda: {"JPM": 19617, "BAC": 70858})
    monkeypatch.setattr(markets_mod, "fetch_facts", lambda symbol, cik: {})
    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {
            "symbol": symbol, "info": {"industry": "Banks - Diversified"},
            "fetch_time": "2026-01-01", "error": None,
        },
    )

    def fake_ffiec(years, **kwargs):
        calls.append((years, kwargs["days_old"]))
        return {years[0]: tmp_path / "latest.zip"}, []

    monkeypatch.setattr(markets_mod, "download_ffiec_years", fake_ffiec)
    monkeypatch.setattr(markets_mod, "ffiec_rssd_ids", lambda path: {1039502, 1073757})
    monkeypatch.setattr(
        markets_mod,
        "parse_ffiec_history",
        lambda rssd: {2024: {"nonperforming_loans_ratio": rssd / 1e9, "cet1_ratio": 0.15}},
    )
    market = _test_market(
        tmp_path, id="snp", fetch_universe=lambda: (["JPM", "BAC"], None), uses_edgar=True,
    )

    result = run_pipeline(
        market, symbols=["jpm", "bac"], days_old=9, fetch_reports=False,
    )

    assert result["fetched"] == 2
    assert len(calls) == 1
    assert calls[0][1] == 9 and len(calls[0][0]) == 5
    company = json.loads((tmp_path / "companies" / "JPM.json").read_text())
    assert company["rssd_id"] == 1039502
    assert company["historical_trends"]["cet1_ratio"] == [0.15]
    company = json.loads((tmp_path / "companies" / "BAC.json").read_text())
    assert company["rssd_id"] == 1073757


def test_pipeline_caches_prices_and_stores_drawdown_in_company_profile(tmp_path, monkeypatch):
    prices = pd.DataFrame(
        {"Adj Close": [100.0, 120.0, 90.0, 110.0]},
        index=pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]),
    )
    monkeypatch.setattr(markets_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(
        markets_mod, "fetch_ticker_data",
        lambda symbol, **kw: {
            "symbol": symbol, "info": {"industry": "Software - Infrastructure"},
            "price_history": prices, "fetch_time": "2026-04-01", "error": None,
        },
    )
    market = _test_market(tmp_path, id="nse", fetch_universe=lambda: (["AAA"], None))

    run_pipeline(market, mode="full-sync", fetch_reports=False)

    company = json.loads((tmp_path / "companies" / "AAA.json").read_text())
    assert company["current_snapshot"]["risk"]["drawdown_52w"] == pytest.approx(-0.25)


def test_final_summary_waits_for_and_reports_annual_report_result(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(
        markets_mod,
        "fetch_ticker_data",
        lambda symbol, **kw: {
            "symbol": symbol,
            "info": {},
            "fetch_time": "2026-01-01",
            "error": None,
        },
    )
    monkeypatch.setattr(markets_mod, "is_report_stale", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        markets_mod,
        "process_symbol_full",
        lambda *args, **kwargs: [{"url": "https://example.test/report.pdf"}],
    )
    monkeypatch.setattr(markets_mod, "_download_and_index_nse", lambda *args: True)
    market = _test_market(tmp_path, fetch_universe=lambda: (["AAA"], None))

    run_pipeline(market, mode="full-sync")

    output = capsys.readouterr().out
    assert "Annual reports stale/missing: 1" in output
    assert "Annual reports: 1 refreshed, 0 unresolved" in output
    assert output.index("Waiting for 1 annual-report job") < output.index("Update complete")
