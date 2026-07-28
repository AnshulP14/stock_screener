"""build_indices' flat screening_summary entry previously carried twin
market_cap_inr/market_cap_usd columns -- market_cap_usd had zero writers
anywhere (confirmed by grep), so it was always null in both the nse and snp
tables. Phase 4 collapses this to a single market_cap + currency pair, with
currency read from the company JSON's now-market-aware top-level field.
"""

import json

from screener.index import build_indices


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

    build_indices(companies_dir=companies_dir, indices_dir=indices_dir)

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

    build_indices(companies_dir=companies_dir, indices_dir=indices_dir)

    summary = json.loads((indices_dir / "screening_summary.json").read_text())
    by_symbol = {c["symbol"]: c for c in summary["companies"]}
    assert by_symbol["RELIANCE"]["currency"] == "INR"
    assert by_symbol["AAPL"]["currency"] == "USD"
