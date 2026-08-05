"""Tests for market-specific universe configuration."""

from screener import market as market_mod


def test_snp_universe_returns_gics_metadata_keyed_by_bare_symbol(monkeypatch):
    monkeypatch.setattr(
        market_mod,
        "fetch_sp500_universe",
        lambda: [
            {"symbol": "AAPL", "company_name": "Apple Inc.", "gics_sector": "Information Technology",
             "gics_industry": "Technology Hardware"},
            {"symbol": "MSFT", "company_name": "Microsoft Corp.", "gics_sector": "Information Technology",
             "gics_industry": "Software"},
        ],
    )

    symbols, metadata = market_mod._snp_universe()

    assert symbols == ["AAPL", "MSFT"]
    assert metadata["AAPL"]["gics_sector"] == "Information Technology"
    assert metadata["MSFT"]["gics_industry"] == "Software"
