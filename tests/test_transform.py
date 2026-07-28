"""build_current_snapshot's field list previously omitted "beta", so
financial_health.beta was always null even though yfinance's `info` blob
carries it and the DB schema already had a column for it. Regression test
for that fix.

Also covers the Phase 4 market_cap/currency unification: build_current_snapshot
and build_company_json used to hardcode "INR"/market_cap_inr regardless of
market, so every S&P profile would have been mislabeled INR once data/snp/
existed. market defaults to NSE (preserving every pre-existing call site
above), but snp.py now passes market=SNP explicitly.
"""

from screener.market import SNP
from screener.transform import build_company_json, build_current_snapshot


def test_beta_is_extracted_from_info():
    data = {
        "symbol": "RELIANCE.NS",
        "info": {"beta": 1.23, "trailingPE": 25.0, "sector": "Energy"},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    snapshot = build_current_snapshot(data)
    assert snapshot["financial_health"]["beta"] == 1.23


def test_beta_absent_from_info_is_null_not_missing():
    data = {
        "symbol": "RELIANCE.NS",
        "info": {"trailingPE": 25.0},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    snapshot = build_current_snapshot(data)
    assert snapshot["financial_health"]["beta"] is None


def test_size_fields_are_unsuffixed_regardless_of_market():
    data = {
        "symbol": "AAPL",
        "info": {"marketCap": 3e12, "enterpriseValue": 3.1e12, "totalRevenue": 4e11},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    snapshot = build_current_snapshot(data, market=SNP)
    assert snapshot["size"]["market_cap"] == 3e12
    assert snapshot["size"]["enterprise_value"] == 3.1e12
    assert snapshot["size"]["total_revenue"] == 4e11


def test_us_company_json_gets_usd_currency_not_hardcoded_inr():
    data = {
        "symbol": "AAPL",
        "info": {"longName": "Apple Inc.", "sector": "Technology"},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    company = build_company_json("AAPL", data, market=SNP)
    assert company["currency"] == "USD"
    assert company["symbol"] == "AAPL"  # SNP ticker_suffix is "" -- no-op strip


def test_nse_symbol_strips_ns_suffix_via_market_ticker_suffix():
    data = {
        "symbol": "RELIANCE.NS",
        "info": {"longName": "Reliance Industries", "sector": "Energy"},
        "fetch_time": "2026-01-01",
        "error": None,
    }
    company = build_company_json("RELIANCE.NS", data)  # default market=NSE
    assert company["symbol"] == "RELIANCE"
    assert company["currency"] == "INR"
