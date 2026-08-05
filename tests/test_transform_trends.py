"""Tests for trend calculations, classifiers, and insights."""

import pandas as pd
import pytest

from screener.transform import (
    average_roe,
    build_historical_trends,
    cagr,
    classify_growth,
    classify_leverage,
    classify_margin_direction,
    generate_insights,
    yoy,
)

FY_ENDS = [pd.Timestamp(f"{year}-03-31") for year in (2022, 2023, 2024)]


def _data(debt=(5.0, 40.0, 160.0), equity=(100.0, 100.0, 100.0)):
    return {
        "symbol": "TEST.NS",
        "info": {},
        "annual_income": pd.DataFrame({
            FY_ENDS[0]: {"Total Revenue": 1000.0, "Net Income": 10.0, "Operating Income": 80.0},
            FY_ENDS[1]: {"Total Revenue": 1000.0, "Net Income": 20.0, "Operating Income": 90.0},
            FY_ENDS[2]: {"Total Revenue": 1000.0, "Net Income": 30.0, "Operating Income": 100.0},
        }),
        "annual_balance": pd.DataFrame({
            FY_ENDS[i]: {"Total Debt": debt[i], "Stockholders Equity": equity[i]}
            for i in range(3)
        }),
        "annual_cashflow": pd.DataFrame(),
    }


@pytest.mark.parametrize(("values", "expected"), [
    ([1, 2], "insufficient_data"),
    ([1, 2, 3, 4], "consistently_growing"),
    ([4, 3, 2, 1], "declining"),
    ([1, 2, 3, 2, 4], "mostly_growing"),
    ([1, 2, 1, 2, 1], "volatile"),
])
def test_classify_growth(values, expected):
    assert classify_growth(values) == expected


@pytest.mark.parametrize(("values", "expected"), [
    ([0.10], "insufficient_data"),
    ([0.10, 0.15, 0.20], "expanding"),
    ([0.20, 0.10, 0.05], "contracting"),
    ([0.10, 0.105, 0.11], "stable"),
])
def test_classify_margin_direction(values, expected):
    assert classify_margin_direction(values) == expected


@pytest.mark.parametrize(("values", "expected"), [
    ([None, None], "insufficient_data"),
    ([0.10, 0.02], "debt_free"),
    ([1.0, 0.30], "low"),
    ([0.30, 0.80], "moderate"),
    ([0.80, 2.0], "high"),
    ([2.0, 0.02], "debt_free"),
])
def test_classify_leverage_uses_latest_non_null_value(values, expected):
    assert classify_leverage(values) == expected


@pytest.mark.parametrize(("values", "window", "expected"), [
    ([0.10, 0.20, 0.30, 0.40], 3, 0.30),
    ([0.10, None, 0.30, 0.50], 3, 0.40),
    ([0.10, None, None], 2, None),
])
def test_average_roe(values, window, expected):
    assert average_roe(values, window=window) == expected


def test_yoy_and_cagr():
    assert yoy([100, 110]) == [None, 0.10]
    assert yoy([0, 110]) == [None, None]
    assert cagr([100, 110, 121]) == pytest.approx(0.10)
    assert cagr([100, None, 121]) == pytest.approx(0.10)
    assert cagr([-100, 121]) is None


def test_historical_trends_use_ratio_margin_and_roe_values():
    trends = build_historical_trends(_data())
    assert trends["debt_to_equity"]["trend"] == "high"
    assert trends["operating_margin"]["values"] == pytest.approx([0.08, 0.09, 0.10])
    assert trends["roe"]["avg_3yr"] == pytest.approx(0.20)


def test_roe_aligns_income_and_equity_by_fiscal_year():
    data = _data()
    data["annual_balance"] = pd.DataFrame({
        FY_ENDS[1]: {"Total Debt": 40.0, "Stockholders Equity": 200.0},
        FY_ENDS[2]: {"Total Debt": 160.0, "Stockholders Equity": 200.0},
    })

    assert build_historical_trends(data)["roe"]["values"] == [
        None, pytest.approx(0.10), pytest.approx(0.15),
    ]


@pytest.mark.parametrize(("debt", "phrase"), [
    ((5.0, 40.0, 160.0), "high leverage"),
    ((50.0, 20.0, 1.0), "debt-free"),
])
def test_leverage_insight_reaches_company_insights(debt, phrase):
    insights = generate_insights(build_historical_trends(_data(debt=debt)))
    assert any(phrase in insight.lower() for insight in insights)
