"""Tests for the Phase 3C flat schema and peer comparisons."""

import pytest

from screener.summary import (
    compute_industry_comparison,
    compute_industry_stats,
    compute_summary_row,
)


def _company(symbol: str, pe: float = 20.0) -> dict:
    years = [2021, 2022, 2023, 2024]
    return {
        "symbol": symbol,
        "company_name": symbol,
        "sector": "Technology",
        "industry": "Software",
        "currency": "USD",
        "current_snapshot": {
            "as_of": "2026-08-06",
            "size": {"market_cap": 1_000.0},
            "price_metrics": {
                "trailing_pe": pe,
                "forward_pe": 18.0,
                "price_to_book": 4.0,
                "enterprise_to_ebitda": 12.0,
            },
            "risk": {"drawdown_52w": -0.25},
        },
        "historical_trends": {
            "fiscal_years": years,
            "revenue": [100.0, 110.0, 120.0, 133.1],
            "diluted_eps": [2.0, 2.2, 2.4, 2.662],
            "operating_margin": [0.10, 0.11, 0.12, 0.15],
            "roe": [None, 0.15, 0.16, 0.17],
            "roa": [None, 0.08, 0.09, 0.10],
            "roce": [None, 0.12, 0.14, 0.16],
            "free_cash_flow": [10.0, -1.0, 12.0, 15.0],
            "net_debt_to_ebitda": [2.0, 1.8, 1.5, 1.2],
            "diluted_shares": [100.0, 99.0, 98.0, 97.0],
            "nonperforming_loans_ratio": [None, None, 0.02, 0.018],
            "cet1_ratio": [None, None, 0.14, 0.15],
        },
    }


def test_summary_projects_latest_annual_values_and_exact_year_signals():
    company = _company("A")
    row = compute_summary_row(company, compute_industry_stats([company]), market="snp")

    assert row["snapshot_as_of"] == "2026-08-06"
    assert row["fundamentals_fy"] == 2024
    assert row["industry_peer_count"] == 0
    assert row["operating_margin"] == 0.15
    assert row["roe"] == 0.17
    assert row["roa"] == 0.10
    assert row["roce"] == 0.16
    assert row["fcf_yield"] == 0.015
    assert row["net_debt_to_ebitda"] == 1.2
    assert row["drawdown_52w"] == -0.25
    assert row["nonperforming_loans_ratio"] == 0.018
    assert row["cet1_ratio"] == 0.15
    assert row["revenue_cagr_3yr"] == pytest.approx(0.10)
    assert row["eps_cagr_3yr"] == pytest.approx(0.10)
    assert row["roce_avg_3yr"] == pytest.approx(0.14)
    assert row["operating_margin_change_3yr"] == pytest.approx(0.05)
    assert row["fcf_positive_years_3yr"] == 2
    assert row["share_count_cagr_3yr"] == pytest.approx((97 / 100) ** (1 / 3) - 1)


def test_signals_require_the_contracts_exact_fiscal_years():
    company = _company("A")
    company["historical_trends"]["fiscal_years"] = [2022, 2024]
    for key, values in list(company["historical_trends"].items()):
        if isinstance(values, list):
            company["historical_trends"][key] = values[1:3]

    row = compute_summary_row(company, compute_industry_stats([company]), market="nse")

    assert row["revenue_cagr_3yr"] is None
    assert row["roce_avg_3yr"] is None
    assert row["operating_margin_change_3yr"] is None
    assert row["fcf_positive_years_3yr"] is None


def test_flat_layout_is_identical_for_nse_and_snp_and_removes_legacy_fields():
    company = _company("A")
    stats = compute_industry_stats([company])
    nse = compute_summary_row(company, stats, market="nse")
    snp = compute_summary_row(company, stats, market="snp")

    assert nse.keys() == snp.keys()
    for removed in (
        "profit_margin", "debt_to_equity", "beta", "net_income_cagr_3yr",
        "pct_insider", "promoter_latest", "margin_percentile",
    ):
        assert removed not in nse


def test_invalid_positive_only_values_are_null():
    company = _company("A", pe=-2.0)
    company["current_snapshot"]["size"]["market_cap"] = 0
    row = compute_summary_row(company, compute_industry_stats([company]), market="snp")

    assert row["trailing_pe"] is None
    assert row["market_cap"] is None
    assert row["fcf_yield"] is None


def test_latest_annual_metric_does_not_backfill_an_older_value():
    company = _company("A")
    company["historical_trends"]["roce"][-1] = None

    row = compute_summary_row(company, compute_industry_stats([company]), market="nse")

    assert row["fundamentals_fy"] == 2024
    assert row["roce"] is None
    assert row["roce_avg_3yr"] is None


def test_percentile_excludes_subject_and_requires_five_valid_peers():
    companies = [_company(chr(65 + index), pe=10.0 + index * 10) for index in range(7)]
    stats = compute_industry_stats(companies)

    lowest = compute_summary_row(companies[0], stats, market="nse")
    middle = compute_summary_row(companies[3], stats, market="nse")

    assert lowest["industry_peer_count"] == 6
    assert lowest["pe_percentile"] == 0.0
    assert middle["pe_percentile"] == 50.0

    five_companies = companies[:5]
    row = compute_summary_row(
        five_companies[0], compute_industry_stats(five_companies), market="nse",
    )
    assert row["pe_percentile"] is None


def test_industry_comparison_retains_peer_context():
    companies = [_company(chr(65 + index), pe=10.0 + index * 10) for index in range(7)]
    comparison = compute_industry_comparison(companies[0], compute_industry_stats(companies))
    pe = comparison["metrics"]["trailing_pe"]

    assert comparison["peer_count"] == 6
    assert pe["value"] == 10.0
    assert pe["industry_median"] == 45.0
    assert pe["valid_peer_count"] == 6
    assert pe["percentile"] == 0.0
