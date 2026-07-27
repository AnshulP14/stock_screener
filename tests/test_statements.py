"""screener.statements — AnnualStatements/AnnualLineItems, the typed
year -> line-item adapter over yfinance's three raw annual DataFrames.

build_historical_trends previously zipped values from different DataFrames
positionally (e.g. net_income from annual_income, stockholders_equity from
annual_balance) rather than looking each up by its own fiscal year. That's
silently wrong whenever the two statements don't cover the same year set --
reproduced below with income covering FY2022-2024 and balance covering only
FY2023-2024, a real yfinance pattern (e.g. restated/unavailable balance-sheet
history near an IPO).
"""

import pandas as pd
import pytest

from screener.statements import AnnualLineItems, AnnualStatements

FY_ENDS = [pd.Timestamp("2022-03-31"), pd.Timestamp("2023-03-31"), pd.Timestamp("2024-03-31")]


# ── AnnualStatements built from literals ────────────────────────────

def test_years_is_sorted_keys_of_by_year():
    stmts = AnnualStatements(by_year={
        2024: AnnualLineItems(revenue=120.0),
        2022: AnnualLineItems(revenue=100.0),
        2023: AnnualLineItems(revenue=110.0),
    })
    assert stmts.years == [2022, 2023, 2024]


def test_named_property_returns_values_ordered_by_year():
    stmts = AnnualStatements(by_year={
        2022: AnnualLineItems(revenue=100.0, stockholders_equity=None),
        2023: AnnualLineItems(revenue=110.0, stockholders_equity=500.0),
    })
    assert stmts.revenue == [100.0, 110.0]
    assert stmts.stockholders_equity == [None, 500.0]


def test_missing_field_on_a_year_defaults_to_none():
    stmts = AnnualStatements(by_year={2022: AnnualLineItems(revenue=100.0)})
    assert stmts.net_income == [None]
    assert stmts.total_debt == [None]


def test_empty_by_year_gives_empty_years_and_series():
    stmts = AnnualStatements(by_year={})
    assert stmts.years == []
    assert stmts.revenue == []


# ── from_yfinance: happy path ────────────────────────────────────────

def _income_df():
    return pd.DataFrame({
        FY_ENDS[0]: {"Total Revenue": 1000.0, "Net Income": 10.0, "Diluted EPS": 1.0,
                     "Gross Profit": 400.0, "Operating Income": 80.0},
        FY_ENDS[1]: {"Total Revenue": 1000.0, "Net Income": 20.0, "Diluted EPS": 2.0,
                     "Gross Profit": 410.0, "Operating Income": 90.0},
        FY_ENDS[2]: {"Total Revenue": 1000.0, "Net Income": 30.0, "Diluted EPS": 3.0,
                     "Gross Profit": 420.0, "Operating Income": 100.0},
    })


def test_from_yfinance_reads_matching_years_across_all_three_statements():
    income = _income_df()
    balance = pd.DataFrame({
        FY_ENDS[0]: {"Total Debt": 5.0, "Stockholders Equity": 100.0},
        FY_ENDS[1]: {"Total Debt": 40.0, "Stockholders Equity": 100.0},
        FY_ENDS[2]: {"Total Debt": 160.0, "Stockholders Equity": 100.0},
    })
    cashflow = pd.DataFrame({
        FY_ENDS[0]: {"Free Cash Flow": 50.0},
        FY_ENDS[1]: {"Free Cash Flow": 60.0},
        FY_ENDS[2]: {"Free Cash Flow": 70.0},
    })

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow)

    assert stmts.years == [2022, 2023, 2024]
    assert stmts.revenue == [1000.0, 1000.0, 1000.0]
    assert stmts.net_income == [10.0, 20.0, 30.0]
    assert stmts.diluted_eps == [1.0, 2.0, 3.0]
    assert stmts.gross_profit == [400.0, 410.0, 420.0]
    assert stmts.operating_income == [80.0, 90.0, 100.0]
    assert stmts.total_debt == [5.0, 40.0, 160.0]
    assert stmts.stockholders_equity == [100.0, 100.0, 100.0]
    assert stmts.free_cash_flow == [50.0, 60.0, 70.0]


# ── from_yfinance: the misalignment regression ───────────────────────

def test_from_yfinance_aligns_by_fiscal_year_not_position():
    # income covers all 3 years; balance covers only the latest 2.
    income = _income_df()
    balance = pd.DataFrame({
        FY_ENDS[1]: {"Total Debt": 40.0, "Stockholders Equity": 500.0},
        FY_ENDS[2]: {"Total Debt": 160.0, "Stockholders Equity": 550.0},
    })
    cashflow = pd.DataFrame()

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow)

    # years_available stays anchored to the income statement...
    assert stmts.years == [2022, 2023, 2024]
    # ...but FY2022 (absent from balance) is None, not shifted from FY2023's
    # balance-sheet data. A positional zip of [40, 160] against 3 income years
    # would have produced [40.0, 160.0, None] here instead.
    assert stmts.stockholders_equity == [None, 500.0, 550.0]
    assert stmts.total_debt == [None, 40.0, 160.0]


def test_from_yfinance_missing_label_entirely_is_all_none():
    income = _income_df()
    balance = pd.DataFrame()  # no balance sheet data at all
    cashflow = pd.DataFrame()

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow)

    assert stmts.total_debt == [None, None, None]
    assert stmts.stockholders_equity == [None, None, None]


def test_from_yfinance_empty_income_gives_no_years():
    stmts = AnnualStatements.from_yfinance(pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    assert stmts.years == []
