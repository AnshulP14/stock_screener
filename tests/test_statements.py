"""Tests for fiscal-year-aligned annual statements."""

import pandas as pd

from screener.market import NSE, SNP
from screener.statements import AnnualLineItems, AnnualStatements, safe_float

FY_ENDS = [pd.Timestamp("2022-03-31"), pd.Timestamp("2023-03-31"), pd.Timestamp("2024-03-31")]


def test_safe_float_rejects_missing_and_non_finite_values():
    assert safe_float("12.5") == 12.5
    assert safe_float(None) is None
    assert safe_float(float("nan")) is None
    assert safe_float(float("inf")) is None


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

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow, NSE.fiscal_year)

    assert stmts.years == [2022, 2023, 2024]
    assert stmts.revenue == [1000.0, 1000.0, 1000.0]
    assert stmts.net_income == [10.0, 20.0, 30.0]
    assert stmts.diluted_eps == [1.0, 2.0, 3.0]
    assert stmts.gross_profit == [400.0, 410.0, 420.0]
    assert stmts.operating_income == [80.0, 90.0, 100.0]
    assert stmts.total_debt == [5.0, 40.0, 160.0]
    assert stmts.stockholders_equity == [100.0, 100.0, 100.0]
    assert stmts.free_cash_flow == [50.0, 60.0, 70.0]


def test_from_yfinance_extracts_phase_3b_base_inputs():
    income = pd.DataFrame({
        FY_ENDS[2]: {
            "Total Revenue": 1000.0,
            "Operating Income": 120.0,
            "Net Income": 80.0,
            "Diluted Average Shares": 40.0,
            "EBITDA": 150.0,
        },
    })
    balance = pd.DataFrame({
        FY_ENDS[2]: {
            "Total Assets": 2000.0,
            "Current Liabilities": 500.0,
            "Cash Cash Equivalents And Short Term Investments": 200.0,
            "Total Debt": 600.0,
            "Stockholders Equity": 900.0,
        },
    })
    cashflow = pd.DataFrame({
        FY_ENDS[2]: {"Operating Cash Flow": 140.0, "Capital Expenditure": -30.0},
    })

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow, NSE.fiscal_year)

    assert stmts.operating_cash_flow == [140.0]
    assert stmts.capex == [-30.0]
    assert stmts.total_assets == [2000.0]
    assert stmts.current_liabilities == [500.0]
    assert stmts.cash_and_equivalents == [200.0]
    assert stmts.diluted_shares == [40.0]
    assert stmts.ebitda == [150.0]


# ── from_yfinance: the misalignment regression ───────────────────────

def test_from_yfinance_aligns_by_fiscal_year_not_position():
    # income covers all 3 years; balance covers only the latest 2.
    income = _income_df()
    balance = pd.DataFrame({
        FY_ENDS[1]: {"Total Debt": 40.0, "Stockholders Equity": 500.0},
        FY_ENDS[2]: {"Total Debt": 160.0, "Stockholders Equity": 550.0},
    })
    cashflow = pd.DataFrame()

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow, NSE.fiscal_year)

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

    stmts = AnnualStatements.from_yfinance(income, balance, cashflow, NSE.fiscal_year)

    assert stmts.total_debt == [None, None, None]
    assert stmts.stockholders_equity == [None, None, None]


def test_from_yfinance_empty_income_gives_no_years():
    stmts = AnnualStatements.from_yfinance(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), NSE.fiscal_year)
    assert stmts.years == []


# ── from_yfinance: fiscal_year_fn is market-specific, not hardcoded ──

def test_us_fiscal_year_uses_calendar_year_of_period_end_not_nse_apr_mar_rule():
    # A Sept 30 period-end: NSE's Apr-Mar rule would label this the *next*
    # calendar year (month >= 4 -> year+1); US convention labels it by the
    # year the period actually ends in, with no adjustment.
    income = pd.DataFrame({
        pd.Timestamp("2023-09-30"): {"Total Revenue": 100.0},
        pd.Timestamp("2024-09-30"): {"Total Revenue": 110.0},
    })
    nse_stmts = AnnualStatements.from_yfinance(income, pd.DataFrame(), pd.DataFrame(), NSE.fiscal_year)
    snp_stmts = AnnualStatements.from_yfinance(income, pd.DataFrame(), pd.DataFrame(), SNP.fiscal_year)

    assert nse_stmts.years == [2024, 2025]  # Sept >= month 4 -> year+1 under NSE's rule
    assert snp_stmts.years == [2023, 2024]   # calendar year of period-end, unadjusted


# ── from_edgar ────────────────────────────────────────────────────────

def _entry(fy, val, *, form="10-K", fp="FY", filed="2024-01-01"):
    return {"form": form, "fp": fp, "fy": fy, "val": val, "filed": filed}


def _facts(**tags):
    return {"facts": {"us-gaap": tags}}


def test_from_edgar_extracts_only_10k_fy_annual_entries():
    facts = _facts(NetIncomeLoss={"units": {"USD": [
        _entry(2023, 100.0),
        _entry(2024, 110.0),
        # a same-year 10-Q datapoint must not be mistaken for the annual figure
        _entry(2024, 30.0, form="10-Q", fp="Q3"),
    ]}})
    stmts = AnnualStatements.from_edgar(facts)
    assert stmts.years == [2023, 2024]
    assert stmts.net_income == [100.0, 110.0]


def test_from_edgar_falls_back_across_renamed_tags():
    # A filer renaming its revenue tag partway through its history (Apple's
    # real pattern: SalesRevenueNet through FY2017, then
    # RevenueFromContractWithCustomerExcludingAssessedTax from FY2018).
    facts = _facts(
        SalesRevenueNet={"units": {"USD": [_entry(2022, 900.0)]}},
        RevenueFromContractWithCustomerExcludingAssessedTax={"units": {"USD": [
            _entry(2023, 1000.0), _entry(2024, 1100.0),
        ]}},
    )
    stmts = AnnualStatements.from_edgar(facts)
    assert stmts.years == [2022, 2023, 2024]
    assert stmts.revenue == [900.0, 1000.0, 1100.0]


def test_from_edgar_a_year_present_in_a_lower_priority_tag_does_not_override_higher_priority():
    facts = _facts(
        RevenueFromContractWithCustomerExcludingAssessedTax={"units": {"USD": [_entry(2023, 1000.0)]}},
        Revenues={"units": {"USD": [_entry(2023, 999.0)]}},  # same year, lower-priority tag
    )
    stmts = AnnualStatements.from_edgar(facts)
    assert stmts.revenue == [1000.0]


def test_from_edgar_restated_entry_for_same_year_keeps_the_latest_filed():
    facts = _facts(NetIncomeLoss={"units": {"USD": [
        _entry(2023, 100.0, filed="2024-01-01"),
        _entry(2023, 105.0, filed="2024-06-01"),  # restatement, filed later
    ]}})
    stmts = AnnualStatements.from_edgar(facts)
    assert stmts.net_income == [105.0]


def test_from_edgar_uses_period_year_not_comparative_filings_fy():
    """Companyfacts repeats prior periods under each later filing's `fy`."""
    facts = _facts(Revenues={"units": {"USD": [
        {**_entry(2023, 383.0, filed="2023-11-03"),
         "start": "2022-09-25", "end": "2023-09-30"},
        {**_entry(2024, 383.0, filed="2024-11-01"),
         "start": "2022-09-25", "end": "2023-09-30"},
        {**_entry(2025, 383.0, filed="2025-10-31"),
         "start": "2022-09-25", "end": "2023-09-30", "frame": "CY2023"},
        {**_entry(2024, 391.0, filed="2024-11-01"),
         "start": "2023-10-01", "end": "2024-09-28"},
        {**_entry(2025, 391.0, filed="2025-10-31"),
         "start": "2023-10-01", "end": "2024-09-28", "frame": "CY2024"},
        {**_entry(2025, 416.0, filed="2025-10-31"),
         "start": "2024-09-29", "end": "2025-09-27", "frame": "CY2025"},
        # Some 10-K companyfacts entries are quarterly durations despite fp=FY.
        {**_entry(2025, 100.0, filed="2025-10-31"),
         "start": "2025-06-29", "end": "2025-09-27", "frame": "CY2025Q3"},
    ]}})

    stmts = AnnualStatements.from_edgar(facts)

    assert stmts.years == [2023, 2024, 2025]
    assert stmts.revenue == [383.0, 391.0, 416.0]


def test_from_edgar_missing_tag_entirely_is_all_none():
    facts = _facts(NetIncomeLoss={"units": {"USD": [_entry(2023, 100.0)]}})
    stmts = AnnualStatements.from_edgar(facts)
    assert stmts.gross_profit == [None]
    assert stmts.total_debt == [None]
    assert stmts.stockholders_equity == [None]
    assert stmts.free_cash_flow == [None]


def test_from_edgar_normalizes_validated_phase_3b_aliases_and_debt_components():
    facts = _facts(
        Revenues={"units": {"USD": [_entry(2024, 1000.0)]}},
        ProfitLoss={"units": {"USD": [_entry(2024, 80.0)]}},
        OperatingIncomeLoss={"units": {"USD": [_entry(2024, 120.0)]}},
        Assets={"units": {"USD": [_entry(2024, 2000.0)]}},
        LiabilitiesCurrent={"units": {"USD": [_entry(2024, 500.0)]}},
        CashAndCashEquivalentsAtCarryingValue={"units": {"USD": [_entry(2024, 200.0)]}},
        StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest={
            "units": {"USD": [_entry(2024, 900.0)]}
        },
        WeightedAverageNumberOfDilutedSharesOutstanding={
            "units": {"shares": [_entry(2024, 40.0)]}
        },
        NetCashProvidedByUsedInOperatingActivities={"units": {"USD": [_entry(2024, 140.0)]}},
        PaymentsToAcquirePropertyPlantAndEquipment={"units": {"USD": [_entry(2024, 30.0)]}},
        DepreciationDepletionAndAmortization={"units": {"USD": [_entry(2024, 30.0)]}},
        LongTermDebtNoncurrent={"units": {"USD": [_entry(2024, 400.0)]}},
        ShortTermBorrowings={"units": {"USD": [_entry(2024, 50.0)]}},
    )

    stmts = AnnualStatements.from_edgar(facts)

    assert stmts.net_income == [80.0]
    assert stmts.total_assets == [2000.0]
    assert stmts.current_liabilities == [500.0]
    assert stmts.cash_and_equivalents == [200.0]
    assert stmts.stockholders_equity == [900.0]
    assert stmts.diluted_shares == [40.0]
    assert stmts.operating_cash_flow == [140.0]
    assert stmts.capex == [30.0]
    assert stmts.ebitda == [150.0]
    assert stmts.total_debt == [450.0]


def test_from_edgar_empty_or_none_facts_gives_no_years():
    assert AnnualStatements.from_edgar(None).years == []
    assert AnnualStatements.from_edgar({}).years == []
    assert AnnualStatements.from_edgar({"facts": {}}).years == []
