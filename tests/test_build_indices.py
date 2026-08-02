"""build_indices' flat screening_summary entry previously carried twin
market_cap_inr/market_cap_usd columns -- market_cap_usd had zero writers
anywhere (confirmed by grep), so it was always null in both the nse and snp
tables. Phase 4 collapses this to a single market_cap + currency pair, with
currency read from the company JSON's now-market-aware top-level field.
"""

import json

from screener.index import build_indices
from screener.store import CompanyStore


def _write_company(companies_dir, symbol, market_cap, currency):
    companies_dir.mkdir(parents=True, exist_ok=True)
    (companies_dir / f"{symbol}.json").write_text(json.dumps({
        "symbol": symbol,
        "company_name": symbol,
        "sector": "Test",
        "industry": "Test",
        "currency": currency,
        "current_snapshot": {"size": {"market_cap": market_cap}},
        "historical_trends": {},
    }))


def test_summary_entry_has_single_market_cap_and_currency(tmp_path):
    companies_dir = tmp_path / "companies"
    indices_dir = tmp_path / "indices"
    _write_company(companies_dir, "AAPL", 3_000_000_000_000.0, "USD")

    build_indices(store=CompanyStore(companies_dir), indices_dir=indices_dir)

    summary = json.loads((indices_dir / "screening_summary.json").read_text())
    entry = summary["companies"][0]
    assert entry["market_cap"] == 3_000_000_000_000.0
    assert entry["currency"] == "USD"
    assert "market_cap_inr" not in entry
    assert "market_cap_usd" not in entry


def test_nse_and_us_companies_keep_their_own_currency(tmp_path):
    companies_dir = tmp_path / "companies"
    indices_dir = tmp_path / "indices"
    _write_company(companies_dir, "RELIANCE", 1.5e13, "INR")
    _write_company(companies_dir, "AAPL", 3e12, "USD")

    build_indices(store=CompanyStore(companies_dir), indices_dir=indices_dir)

    summary = json.loads((indices_dir / "screening_summary.json").read_text())
    by_symbol = {c["symbol"]: c for c in summary["companies"]}
    assert by_symbol["RELIANCE"]["currency"] == "INR"
    assert by_symbol["AAPL"]["currency"] == "USD"


# ── industry_comparison write-back (Phase 6) ──────────────────────────

def _write_company_with_pe(companies_dir, symbol, industry, pe):
    companies_dir.mkdir(parents=True, exist_ok=True)
    (companies_dir / f"{symbol}.json").write_text(json.dumps({
        "symbol": symbol,
        "company_name": symbol,
        "sector": "Test",
        "industry": industry,
        "currency": "USD",
        "current_snapshot": {"price_metrics": {"trailing_pe": pe}, "size": {}},
        "historical_trends": {},
        "industry_comparison": None,
    }))


def test_build_indices_writes_industry_comparison_back_onto_company_files(tmp_path):
    companies_dir = tmp_path / "companies"
    indices_dir = tmp_path / "indices"
    _write_company_with_pe(companies_dir, "A", "Software", pe=10.0)
    _write_company_with_pe(companies_dir, "B", "Software", pe=30.0)

    build_indices(store=CompanyStore(companies_dir), indices_dir=indices_dir)

    a = json.loads((companies_dir / "A.json").read_text())
    assert a["industry_comparison"]["industry"] == "Software"
    assert a["industry_comparison"]["peer_count"] == 2
    assert a["industry_comparison"]["metrics"]["trailing_pe"]["value"] == 10.0


def test_build_indices_industry_comparison_populated_for_snp_too(tmp_path):
    """Previously NSE-only per data/SCHEMA.md; both markets now get it since
    the underlying industry_stats already exist for both."""
    companies_dir = tmp_path / "companies"
    indices_dir = tmp_path / "indices"
    _write_company_with_pe(companies_dir, "AAPL", "Consumer Electronics", pe=40.0)

    build_indices(store=CompanyStore(companies_dir), indices_dir=indices_dir)

    aapl = json.loads((companies_dir / "AAPL.json").read_text())
    assert aapl["industry_comparison"] is not None
    assert aapl["industry_comparison"]["peer_count"] == 1
