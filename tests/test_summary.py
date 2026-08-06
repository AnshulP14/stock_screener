"""Tests for flat summaries and industry comparisons."""

import pytest

from screener.summary import (
    compute_industry_comparison,
    compute_industry_stats,
    compute_summary_row,
)


def _company(symbol, industry, pe, margin, cagr=None, cik=None, institutional_ownership=None):
    return {
        "symbol": symbol,
        "company_name": symbol,
        "sector": "Tech",
        "industry": industry,
        "currency": "USD",
        "cik": cik,
        "current_snapshot": {
            "price_metrics": {"trailing_pe": pe},
            "profitability": {"profit_margin": margin},
        },
        "historical_trends": {"revenue": {"cagr_3yr": cagr}} if cagr is not None else {},
        "institutional_ownership": institutional_ownership,
    }


# ── compute_industry_stats ───────────────────────────────────────────

def test_industry_stats_needs_at_least_two_values_for_a_metric():
    companies = [_company("A", "Software", pe=20.0, margin=0.1)]
    stats = compute_industry_stats(companies)
    assert stats["Software"]["company_count"] == 1
    assert stats["Software"]["metrics"]["pe"] is None


def test_industry_stats_computes_median_with_two_or_more_values():
    companies = [
        _company("A", "Software", pe=10.0, margin=0.1),
        _company("B", "Software", pe=30.0, margin=0.2),
    ]
    stats = compute_industry_stats(companies)
    assert stats["Software"]["metrics"]["pe"]["median"] == 20.0
    assert stats["Software"]["metrics"]["pe"]["count"] == 2


def test_industry_stats_groups_separately_by_industry():
    companies = [
        _company("A", "Software", pe=10.0, margin=0.1),
        _company("B", "Hardware", pe=50.0, margin=0.3),
    ]
    stats = compute_industry_stats(companies)
    assert stats["Software"]["company_count"] == 1
    assert stats["Hardware"]["company_count"] == 1


# ── compute_summary_row ───────────────────────────────────────────────

def test_summary_row_has_core_fields():
    companies = [_company("A", "Software", pe=20.0, margin=0.1, cagr=0.15)]
    stats = compute_industry_stats(companies)
    row = compute_summary_row(companies[0], stats, market="snp")
    assert row["symbol"] == "A"
    assert row["industry"] == "Software"
    assert row["trailing_pe"] == 20.0
    assert row["revenue_cagr_3yr"] == 0.15


def test_summary_row_percentile_present_with_enough_peers():
    companies = [
        _company("A", "Software", pe=10.0, margin=0.1),
        _company("B", "Software", pe=30.0, margin=0.2),
    ]
    stats = compute_industry_stats(companies)
    row = compute_summary_row(companies[0], stats, market="snp")
    assert row["pe_percentile"] is not None


def test_summary_row_percentile_none_with_a_single_peer():
    companies = [_company("A", "Software", pe=10.0, margin=0.1)]
    stats = compute_industry_stats(companies)
    row = compute_summary_row(companies[0], stats, market="snp")
    assert row["pe_percentile"] is None


def test_summary_row_carries_cik_and_institutional_ownership_pct_fields():
    io = {"pct_insider": 1.6, "pct_institutional": 66.5, "top_holders": []}
    companies = [_company("AAPL", "Tech", pe=20.0, margin=0.1, cik=320193, institutional_ownership=io)]
    stats = compute_industry_stats(companies)
    row = compute_summary_row(companies[0], stats, market="snp")
    assert row["cik"] == 320193
    assert row["pct_insider"] == 1.6
    assert row["pct_institutional"] == 66.5


def test_nse_summary_row_omits_snp_only_fields():
    companies = [_company("RELIANCE", "Energy", pe=20.0, margin=0.1)]
    stats = compute_industry_stats(companies)
    row = compute_summary_row(companies[0], stats, market="nse")
    assert "cik" not in row
    assert "pct_insider" not in row
    assert "pct_institutional" not in row


def test_summary_row_shareholding_latest_and_trend():
    company = _company("A", "Software", pe=20.0, margin=0.1)
    company["shareholding"] = {
        "promoter": [50.0, 52.3],
        "trends": {"promoter": "increasing"},
    }
    stats = compute_industry_stats([company])
    row = compute_summary_row(company, stats, market="nse")
    assert row["promoter_latest"] == 52.3
    assert row["promoter_trend"] == "increasing"


def test_summary_row_handles_missing_shareholding_key():
    company = _company("A", "Software", pe=20.0, margin=0.1)
    stats = compute_industry_stats([company])
    row = compute_summary_row(company, stats, market="nse")
    assert row["promoter_latest"] is None
    assert row["promoter_trend"] is None


# ── compute_industry_comparison ──────────────────────────────────────

def test_industry_comparison_has_value_median_percentile_vs_median():
    companies = [
        _company("A", "Software", pe=10.0, margin=0.1),
        _company("B", "Software", pe=30.0, margin=0.2),
    ]
    stats = compute_industry_stats(companies)
    comp = compute_industry_comparison(companies[0], stats)

    assert comp["industry"] == "Software"
    assert comp["peer_count"] == 2
    pe = comp["metrics"]["trailing_pe"]
    assert pe["value"] == 10.0
    assert pe["industry_median"] == 20.0
    assert pe["vs_median"] == pytest.approx(-0.5)  # 10 is 50% below the median of 20


def test_industry_comparison_metric_is_none_without_enough_peers():
    companies = [_company("A", "Software", pe=10.0, margin=0.1)]
    stats = compute_industry_stats(companies)
    comp = compute_industry_comparison(companies[0], stats)
    assert comp["peer_count"] == 1
    assert comp["metrics"]["trailing_pe"] is None
