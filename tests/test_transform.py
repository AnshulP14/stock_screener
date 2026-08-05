"""Tests for market-aware company transforms."""

import pandas as pd
import pytest

from screener.market import NSE, SNP
from screener.transform import (
    build_company_json,
    build_current_snapshot,
    build_historical_trends_edgar,
    build_institutional_ownership,
)


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


def test_company_json_defaults_cik_and_institutional_ownership_to_none():
    data = {"symbol": "RELIANCE.NS", "info": {}, "fetch_time": "2026-01-01", "error": None}
    company = build_company_json("RELIANCE.NS", data)
    assert company["cik"] is None
    assert company["institutional_ownership"] is None


def test_company_json_carries_through_cik_and_institutional_ownership():
    data = {"symbol": "AAPL", "info": {}, "fetch_time": "2026-01-01", "error": None}
    io = {"updated_at": "2026-01-01", "pct_insider": 1.6, "pct_institutional": 66.5, "top_holders": []}
    company = build_company_json("AAPL", data, market=SNP, cik=320193, institutional_ownership=io)
    assert company["cik"] == 320193
    assert company["institutional_ownership"] == io


# ── MarketConfig.metadata_fields (isin/nse_industry/gics_sector/gics_industry) ──

def test_nse_company_json_gets_isin_and_nse_industry_from_metadata():
    data = {"symbol": "RELIANCE.NS", "info": {}, "fetch_time": "2026-01-01", "error": None}
    metadata = {"RELIANCE.NS": {"isin_code": "INE002A01018", "nse_industry": "Oil & Gas"}}
    company = build_company_json("RELIANCE.NS", data, metadata=metadata, market=NSE)
    assert company["isin"] == "INE002A01018"
    assert company["nse_industry"] == "Oil & Gas"
    assert "gics_sector" not in company


def test_snp_company_json_gets_gics_sector_and_industry_from_metadata():
    data = {"symbol": "AAPL", "info": {}, "fetch_time": "2026-01-01", "error": None}
    metadata = {"AAPL": {"gics_sector": "Information Technology", "gics_industry": "Technology Hardware"}}
    company = build_company_json("AAPL", data, metadata=metadata, market=SNP)
    assert company["gics_sector"] == "Information Technology"
    assert company["gics_industry"] == "Technology Hardware"
    assert "isin" not in company


def test_metadata_fields_default_to_none_when_symbol_missing_from_metadata():
    data = {"symbol": "RELIANCE.NS", "info": {}, "fetch_time": "2026-01-01", "error": None}
    company = build_company_json("RELIANCE.NS", data, metadata=None, market=NSE)
    assert company["isin"] is None
    assert company["nse_industry"] is None


# ── build_historical_trends_edgar ────────────────────────────────────

def _edgar_entry(fy, val):
    return {"form": "10-K", "fp": "FY", "fy": fy, "val": val, "filed": "2024-01-01"}


def _edgar_facts():
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_edgar_entry(2023, 1000.0), _edgar_entry(2024, 1100.0)]}},
        "NetIncomeLoss": {"units": {"USD": [_edgar_entry(2023, 100.0), _edgar_entry(2024, 121.0)]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [_edgar_entry(2023, 5.0), _edgar_entry(2024, 6.0)]}},
        "GrossProfit": {"units": {"USD": [_edgar_entry(2023, 400.0), _edgar_entry(2024, 450.0)]}},
        "OperatingIncomeLoss": {"units": {"USD": [_edgar_entry(2023, 80.0), _edgar_entry(2024, 99.0)]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _edgar_entry(2023, -10.0), _edgar_entry(2024, 150.0),
        ]}},
    }}}


def test_build_historical_trends_edgar_source_is_edgar_xbrl():
    trends = build_historical_trends_edgar(_edgar_facts())
    assert trends["source"] == "edgar_xbrl"
    assert trends["years_available"] == [2023, 2024]


def test_build_historical_trends_edgar_has_no_roe_or_debt_fields():
    # SNP lacks balance-sheet data in EDGAR, so composites are absent.
    trends = build_historical_trends_edgar(_edgar_facts())
    assert "roe" not in trends
    assert "debt_to_equity" not in trends
    assert "free_cash_flow" not in trends
    # Revenue still gets yoy_growth from the unified builder
    assert "yoy_growth" in trends["revenue"]


def test_build_historical_trends_edgar_gross_profit_and_ocf_use_values_key():
    # Unified output key: "values" for both markets (currency is on company JSON top level).
    trends = build_historical_trends_edgar(_edgar_facts())
    assert trends["gross_profit"]["values"] == [400.0, 450.0]
    assert trends["operating_cash_flow"]["values"] == [-10.0, 150.0]
    assert trends["operating_cash_flow"]["positive_years"] == 1


def test_build_historical_trends_edgar_operating_margin_has_values_and_direction():
    trends = build_historical_trends_edgar(_edgar_facts())
    assert trends["operating_margin"]["values"] == pytest.approx([0.08, 0.09])
    assert "direction" in trends["operating_margin"]


def test_build_historical_trends_edgar_no_data_returns_error_marker():
    trends = build_historical_trends_edgar(None)
    assert trends == {"source": "edgar_xbrl", "years_available": [], "error": "no_data"}


# ── build_institutional_ownership ────────────────────────────────────

def test_institutional_ownership_reads_pct_fields_from_info_as_whole_numbers():
    data = {"info": {"heldPercentInsiders": 0.0163, "heldPercentInstitutions": 0.66496}}
    io = build_institutional_ownership(data)
    assert io["pct_insider"] == pytest.approx(1.63)
    assert io["pct_institutional"] == pytest.approx(66.496)


def test_institutional_ownership_top_holders_from_institutional_holders_df():
    df = pd.DataFrame([
        {"Holder": "Blackrock Inc.", "Shares": 1144695425, "pctHeld": 0.0779},
        {"Holder": "Vanguard Capital Management LLC", "Shares": 953847648, "pctHeld": 0.0649},
    ])
    data = {"info": {}, "institutional_holders": df}
    io = build_institutional_ownership(data)
    assert io["top_holders"][0] == {"holder": "Blackrock Inc.", "shares": 1144695425.0, "pct_out": pytest.approx(7.79)}
    assert len(io["top_holders"]) == 2


def test_institutional_ownership_returns_none_when_nothing_fetched():
    assert build_institutional_ownership({"info": {}}) is None
    assert build_institutional_ownership({"info": {}, "institutional_holders": pd.DataFrame()}) is None
