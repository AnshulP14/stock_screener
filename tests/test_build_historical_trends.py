"""build_historical_trends previously wired debt_to_equity.trend to
_classify_margin(debt_vals) — the raw absolute debt figures, run through a
delta-based classifier meant for margins — instead of the debt/equity ratio
through a level-based leverage classifier. It also stored YoY change under
operating_margin.values (a key documented and named for the margin itself),
and computed roe.avg_3yr as net-income CAGR standing in for an ROE average.

Fixture numbers are chosen so the old and fixed code disagree, not just so
the field is present: they aren't derived by re-running the code under test.
"""

import pandas as pd
import pytest

from screener.transform import build_historical_trends
from screener.trends import LeverageBand

FY_ENDS = [pd.Timestamp("2022-03-31"), pd.Timestamp("2023-03-31"), pd.Timestamp("2024-03-31")]


def _fixture_data():
    annual_income = pd.DataFrame({
        FY_ENDS[0]: {"Total Revenue": 1000.0, "Net Income": 10.0, "Operating Income": 80.0},
        FY_ENDS[1]: {"Total Revenue": 1000.0, "Net Income": 20.0, "Operating Income": 90.0},
        FY_ENDS[2]: {"Total Revenue": 1000.0, "Net Income": 30.0, "Operating Income": 100.0},
    })
    annual_balance = pd.DataFrame({
        # Debt grows in absolute terms (5 -> 40 -> 160) but equity grows even
        # faster in the first years, so the ratio itself is 0.05 -> 0.40 -> 1.60.
        # A delta-based classifier on raw debt alone would call this "expanding"
        # (change of 155, nowhere near the ratio-appropriate 0.05/0.5/1.5 bands).
        FY_ENDS[0]: {"Total Debt": 5.0, "Stockholders Equity": 100.0},
        FY_ENDS[1]: {"Total Debt": 40.0, "Stockholders Equity": 100.0},
        FY_ENDS[2]: {"Total Debt": 160.0, "Stockholders Equity": 100.0},
    })
    return {
        "symbol": "TEST.NS",
        "info": {"sector": "Test", "industry": "Test"},
        "annual_income": annual_income,
        "annual_balance": annual_balance,
        "annual_cashflow": pd.DataFrame(),
    }


def test_debt_to_equity_trend_classifies_the_ratio_level_not_raw_debt_delta():
    trends = build_historical_trends(_fixture_data())
    # Latest ratio 160/100 = 1.60 -> HIGH. The old wiring read raw debt_vals
    # through a delta classifier and could never produce a LeverageBand at all.
    assert trends["debt_to_equity"]["trend"] == LeverageBand.HIGH


def test_operating_margin_values_holds_the_margin_not_its_yoy_change():
    trends = build_historical_trends(_fixture_data())
    # Operating Income / Total Revenue = 80/1000, 90/1000, 100/1000.
    assert trends["operating_margin"]["values"] == pytest.approx([0.08, 0.09, 0.10])


def test_roe_avg_3yr_is_the_actual_roe_average_not_net_income_cagr():
    trends = build_historical_trends(_fixture_data())
    # Net Income / Stockholders Equity = 10/100, 20/100, 30/100 -> mean 0.20.
    # Net-income CAGR over the same years is (30/10)**(1/2)-1 ~= 0.732 -- a
    # very different number, so this pins the actual metric, not just "a value".
    assert trends["roe"]["avg_3yr"] == pytest.approx(0.20)
