"""End-to-end: build_historical_trends -> generate_insights. This is the
actual regression test for "0 of 500 companies in the live DB got a leverage
insight" — debt_to_equity.trend was wired to the wrong classifier over the
wrong data, so it could never produce a value generate_insights matched on.
"""

import pandas as pd

from screener.transform import build_historical_trends, generate_insights

FY_ENDS = [pd.Timestamp("2022-03-31"), pd.Timestamp("2023-03-31"), pd.Timestamp("2024-03-31")]


def _fixture_data(debt_by_year, equity_by_year):
    annual_income = pd.DataFrame({
        FY_ENDS[0]: {"Total Revenue": 1000.0, "Net Income": 10.0},
        FY_ENDS[1]: {"Total Revenue": 1000.0, "Net Income": 20.0},
        FY_ENDS[2]: {"Total Revenue": 1000.0, "Net Income": 30.0},
    })
    annual_balance = pd.DataFrame({
        FY_ENDS[i]: {"Total Debt": debt_by_year[i], "Stockholders Equity": equity_by_year[i]}
        for i in range(3)
    })
    return {
        "symbol": "TEST.NS",
        "info": {},
        "annual_income": annual_income,
        "annual_balance": annual_balance,
        "annual_cashflow": pd.DataFrame(),
    }


def test_high_leverage_insight_reaches_key_insights():
    # Latest debt/equity = 160/100 = 1.60 -> HIGH.
    trends = build_historical_trends(_fixture_data([5, 40, 160], [100, 100, 100]))
    insights = generate_insights(trends)
    assert any("high leverage" in i.lower() for i in insights)


def test_debt_free_insight_reaches_key_insights():
    # Latest debt/equity = 1/100 = 0.01 -> DEBT_FREE.
    trends = build_historical_trends(_fixture_data([50, 20, 1], [100, 100, 100]))
    insights = generate_insights(trends)
    assert any("debt-free" in i.lower() for i in insights)
