"""Year-aligned annual statements from Yahoo or SEC EDGAR."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


def safe_float(value: Any) -> float | None:
    """Convert to a finite float, or return None."""
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _process_annual_statement(
    df: pd.DataFrame, fiscal_year_fn: Callable[[pd.Timestamp], int]
) -> pd.DataFrame:
    """Transpose annual statement, collapse columns to fiscal years."""
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        transposed = df.T.copy()
        transposed.index = pd.to_datetime(transposed.index)
        transposed["fiscal_year"] = transposed.index.to_series().apply(fiscal_year_fn)
        return transposed.groupby("fiscal_year").last().T
    except Exception:
        return pd.DataFrame()


def _cell(df: pd.DataFrame, label: str, year: int) -> float | None:
    if label not in df.index or year not in df.columns:
        return None
    value = df.loc[label, year]
    if isinstance(value, pd.Series):
        value = value.iloc[0]
    return safe_float(value)


@dataclass(frozen=True)
class AnnualLineItems:
    revenue: float | None = None
    net_income: float | None = None
    diluted_eps: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    free_cash_flow: float | None = None
    total_debt: float | None = None
    stockholders_equity: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    total_assets: float | None = None
    current_liabilities: float | None = None
    cash_and_equivalents: float | None = None
    diluted_shares: float | None = None
    ebitda: float | None = None


# (field name, source statement, yfinance row label) -- the only place these
# labels appear.
_FIELD_SOURCES = [
    ("revenue", "income", "Total Revenue"),
    ("net_income", "income", "Net Income"),
    ("diluted_eps", "income", "Diluted EPS"),
    ("gross_profit", "income", "Gross Profit"),
    ("operating_income", "income", "Operating Income"),
    ("free_cash_flow", "cashflow", "Free Cash Flow"),
    ("total_debt", "balance", "Total Debt"),
    ("stockholders_equity", "balance", "Stockholders Equity"),
    ("operating_cash_flow", "cashflow", "Operating Cash Flow"),
    ("capex", "cashflow", "Capital Expenditure"),
    ("total_assets", "balance", "Total Assets"),
    ("current_liabilities", "balance", "Current Liabilities"),
    ("cash_and_equivalents", "balance", "Cash Cash Equivalents And Short Term Investments"),
    ("diluted_shares", "income", "Diluted Average Shares"),
    ("ebitda", "income", "EBITDA"),
]

# Candidate SEC tags are ordered so later aliases only fill missing years.
_EDGAR_FIELD_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "total_assets": ("Assets",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ),
    "stockholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}

_EDGAR_DEBT_TAGS = {
    "direct": ("LongTermDebt", "DebtAndFinanceLeaseObligations"),
    "noncurrent": (
        "LongTermDebtNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    ),
    "current": (
        "LongTermDebtCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "ShortTermBorrowings",
    ),
}

_EDGAR_DA_TAGS = (
    "DepreciationDepletionAndAmortization",
    "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
)


def _edgar_annual_values(tag_facts: dict) -> dict[int, float]:
    """Return fiscal-year values from a GAAP tag's annual 10-K entries."""
    periods: dict[str, list[dict]] = {}
    for unit_values in tag_facts.get("units", {}).values():
        for entry in unit_values:
            if entry.get("form") != "10-K" or entry.get("fp") != "FY":
                continue
            fy, val = entry.get("fy"), entry.get("val")
            if fy is None or val is None:
                continue
            start, end = entry.get("start"), entry.get("end")
            if start and end:
                try:
                    if (date.fromisoformat(end) - date.fromisoformat(start)).days < 300:
                        continue
                except ValueError:
                    pass
            periods.setdefault(end or f"fy:{fy}", []).append(entry)

    values = {}
    for entries in periods.values():
        first = min(entries, key=lambda entry: entry.get("filed", ""))
        latest = max(entries, key=lambda entry: entry.get("filed", ""))
        values[int(first["fy"])] = latest["val"]
    return values


def _edgar_field_values(us_gaap: dict, field: str) -> dict[int, float]:
    """Merge candidate tags by priority, filling only missing years."""
    values: dict[int, float] = {}
    for tag in _EDGAR_FIELD_TAGS[field]:
        tag_facts = us_gaap.get(tag)
        if not tag_facts:
            continue
        for fy, val in _edgar_annual_values(tag_facts).items():
            values.setdefault(fy, val)
    return values


def _edgar_tag_values(us_gaap: dict, tags: tuple[str, ...]) -> dict[int, float]:
    values: dict[int, float] = {}
    for tag in tags:
        for fy, val in _edgar_annual_values(us_gaap.get(tag, {})).items():
            values.setdefault(fy, val)
    return values


@dataclass(frozen=True)
class AnnualStatements:
    by_year: dict[int, AnnualLineItems]

    _LINE_ITEMS = tuple(AnnualLineItems.__dataclass_fields__)  # type: ignore[has-type]

    @property
    def years(self) -> list[int]:
        return sorted(self.by_year)

    def __getattr__(self, name: str) -> list[float | None]:
        if name in self._LINE_ITEMS:
            return [self.by_year[y].__getattribute__(name) for y in self.years]
        raise AttributeError(f"{type(self).__name__!r} has no attribute {name!r}")

    @classmethod
    def from_yfinance(
        cls,
        income: pd.DataFrame,
        balance: pd.DataFrame,
        cashflow: pd.DataFrame,
        fiscal_year_fn: Callable[[pd.Timestamp], int],
    ) -> AnnualStatements:
        """Align Yahoo statements to income-statement fiscal years."""
        income = _process_annual_statement(income, fiscal_year_fn)
        balance = _process_annual_statement(balance, fiscal_year_fn)
        cashflow = _process_annual_statement(cashflow, fiscal_year_fn)
        sources = {"income": income, "balance": balance, "cashflow": cashflow}

        by_year = {}
        for year in sorted(income.columns):
            fields = {
                field: _cell(sources[source], label, year)
                for field, source, label in _FIELD_SOURCES
            }
            by_year[year] = AnnualLineItems(**fields)
        return cls(by_year=by_year)

    @classmethod
    def from_edgar(cls, facts: dict | None) -> AnnualStatements:
        """Extract supported annual fields from SEC companyfacts."""
        us_gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
        per_field = {field: _edgar_field_values(us_gaap, field) for field in _EDGAR_FIELD_TAGS}

        direct_debt = _edgar_tag_values(us_gaap, _EDGAR_DEBT_TAGS["direct"])
        noncurrent_debt = _edgar_tag_values(us_gaap, _EDGAR_DEBT_TAGS["noncurrent"])
        current_debt = _edgar_tag_values(us_gaap, _EDGAR_DEBT_TAGS["current"])
        debt_years = set(direct_debt) | (set(noncurrent_debt) & set(current_debt))
        per_field["total_debt"] = {
            year: (
                direct_debt[year]
                if year in direct_debt
                else noncurrent_debt[year] + current_debt[year]
            )
            for year in debt_years
        }

        depreciation = _edgar_tag_values(us_gaap, _EDGAR_DA_TAGS)
        operating_income = per_field["operating_income"]
        per_field["ebitda"] = {
            year: operating_income[year] + depreciation[year]
            for year in set(operating_income) & set(depreciation)
        }

        years = sorted(set().union(*per_field.values()))
        by_year = {
            year: AnnualLineItems(**{field: values.get(year) for field, values in per_field.items()})
            for year in years
        }
        return cls(by_year=by_year)
