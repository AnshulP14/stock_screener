"""Tests for market-aware company transforms."""

import pandas as pd
import pytest

from screener.market import NSE, SNP
from screener.statements import AnnualLineItems, AnnualStatements
from screener.transform import (
    build_company_json,
    build_current_snapshot,
    build_historical_trends_edgar,
    build_institutional_ownership,
    build_trends,
    drawdown_52w,
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


def test_drawdown_52w_reads_cached_adjusted_prices_and_company_profile_stores_it(tmp_path):
    path = tmp_path / "AAA.csv"
    pd.DataFrame({
        "date": ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
        "adjusted_close": [100.0, 120.0, 90.0, 110.0],
    }).to_csv(path, index=False)

    value = drawdown_52w(path)
    company = build_company_json(
        "AAPL", {"symbol": "AAPL", "info": {}}, market=SNP, drawdown=value,
    )

    assert value == pytest.approx(-0.25)
    assert company["current_snapshot"]["risk"]["drawdown_52w"] == pytest.approx(-0.25)


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
    assert trends["fiscal_years"] == [2023, 2024]


def test_build_historical_trends_edgar_preserves_missing_balance_metrics_as_aligned_nulls():
    trends = build_historical_trends_edgar(_edgar_facts())
    assert trends["roe"] == [None, None]
    assert trends["roa"] == [None, None]
    assert trends["net_debt_to_ebitda"] == [None, None]


def test_build_historical_trends_edgar_uses_direct_aligned_arrays():
    trends = build_historical_trends_edgar(_edgar_facts())
    assert trends["gross_profit"] == [400.0, 450.0]
    assert trends["operating_cash_flow"] == [-10.0, 150.0]


def test_build_historical_trends_edgar_operating_margin_is_an_aligned_array():
    trends = build_historical_trends_edgar(_edgar_facts())
    assert trends["operating_margin"] == pytest.approx([0.08, 0.09])


def test_build_historical_trends_edgar_no_data_returns_error_marker():
    trends = build_historical_trends_edgar(None)
    assert trends == {"source": "edgar_xbrl", "fiscal_years": [], "error": "no_data"}


def test_build_historical_trends_edgar_merges_regulatory_years():
    trends = build_historical_trends_edgar(
        _edgar_facts(),
        regulatory={2024: {"nonperforming_loans_ratio": 0.02, "cet1_ratio": 0.14}},
    )

    assert trends["nonperforming_loans_ratio"] == [None, 0.02]
    assert trends["cet1_ratio"] == [None, 0.14]


def test_build_trends_produces_aligned_phase_3b_series_and_formulas():
    statements = AnnualStatements(by_year={
        2022: AnnualLineItems(
            revenue=1000, gross_profit=400, operating_income=100, net_income=80,
            operating_cash_flow=120, capex=-20, total_assets=1000,
            current_liabilities=200, cash_and_equivalents=100, total_debt=300,
            stockholders_equity=500, diluted_shares=10, ebitda=150,
        ),
        2023: AnnualLineItems(
            revenue=1100, gross_profit=440, operating_income=121, net_income=88,
            operating_cash_flow=130, capex=30, total_assets=1200,
            current_liabilities=220, cash_and_equivalents=110, total_debt=330,
            stockholders_equity=550, diluted_shares=9.5, ebitda=160,
        ),
        2024: AnnualLineItems(
            revenue=1200, gross_profit=480, operating_income=144, net_income=96,
            operating_cash_flow=140, capex=40, total_assets=1400,
            current_liabilities=240, cash_and_equivalents=120, total_debt=360,
            stockholders_equity=600, diluted_shares=9, ebitda=180,
        ),
    })
    regulatory = {
        2023: {"nonperforming_loans_ratio": 0.02, "cet1_ratio": 0.14, "loans": 700},
        2024: {"nonperforming_loans_ratio": 0.018, "cet1_ratio": 0.15, "loans": 760},
    }

    trends = build_trends(statements, (), source="test", regulatory=regulatory)

    assert trends["fiscal_years"] == [2022, 2023, 2024]
    assert trends["free_cash_flow"] == [100, 100, 100]
    assert trends["operating_margin"] == pytest.approx([0.10, 0.11, 0.12])
    assert trends["roe"] == [None, pytest.approx(88 / 525), pytest.approx(96 / 575)]
    assert trends["roa"] == [None, pytest.approx(88 / 1100), pytest.approx(96 / 1300)]
    assert trends["roce"] == [None, pytest.approx(121 / 890), pytest.approx(144 / 1070)]
    assert trends["net_debt"] == [200, 220, 240]
    assert trends["net_debt_to_ebitda"] == pytest.approx([200 / 150, 220 / 160, 240 / 180])
    assert trends["nonperforming_loans_ratio"] == [None, 0.02, 0.018]
    assert trends["cet1_ratio"] == [None, 0.14, 0.15]
    assert all(len(trends[field]) == 3 for field in (
        "revenue", "capex", "fcf_margin", "cfo_to_net_income", "capex_intensity",
        "loans", "deposits",
    ))


def test_average_balance_returns_require_consecutive_fiscal_years():
    statements = AnnualStatements(by_year={
        2022: AnnualLineItems(
            net_income=80, operating_income=100, total_assets=1000,
            current_liabilities=200, stockholders_equity=500,
        ),
        2024: AnnualLineItems(
            net_income=96, operating_income=144, total_assets=1400,
            current_liabilities=240, stockholders_equity=600,
        ),
    })

    trends = build_trends(statements, (), source="test")

    assert trends["roe"] == [None, None]
    assert trends["roa"] == [None, None]
    assert trends["roce"] == [None, None]


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
