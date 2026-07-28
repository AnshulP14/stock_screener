"""AnnualStatements — typed year -> line-item adapter over yfinance's three
raw annual DataFrames (income, balance sheet, cashflow).

build_historical_trends used to pull each line item out with its own
_row_values(df, label) call and zip the resulting lists positionally (e.g.
net_income from annual_income zipped against stockholders_equity from
annual_balance). That's silently wrong whenever two statements don't cover
the same fiscal years — common near IPOs or restatements, where yfinance
returns a shorter balance-sheet history than the income statement. Looking
each line item up by its own fiscal year, as from_yfinance does below, fixes
that: a year missing from one statement yields None for that field in that
year rather than shifting every later value by one position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None  # NaN != NaN
    except (ValueError, TypeError):
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
    return _safe_float(value)


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
]

# field name -> candidate SEC XBRL us-gaap tags, in priority order. More than
# one tag exists per concept because filers change tags over time (e.g.
# Apple filed revenue under SalesRevenueNet through FY2017, then switched to
# RevenueFromContractWithCustomerExcludingAssessedTax from FY2018 onward) --
# the only place these tag names appear.
_EDGAR_FIELD_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "net_income": ("NetIncomeLoss",),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
}


def _edgar_annual_values(tag_facts: dict) -> dict[int, float]:
    """fiscal year -> value, from one GAAP tag's annual entries.

    Keyed by EDGAR's own `fy` (only `form == "10-K"` and `fp == "FY"` entries
    count -- everything else in a tag's unit list is a 10-Q or a restated
    prior period reported alongside a later quarter). This is EDGAR's own
    fiscal-year label, not one recomputed from a period-end date: SEC's fy/fp
    already reflects each filer's actual fiscal calendar, including
    non-calendar year-ends, which a single date-based rule can't (see
    MarketConfig.fiscal_year's docstring). When a tag carries two annual
    entries for the same fy (a restatement), the one filed most recently wins.
    """
    best: dict[int, tuple[str, float]] = {}
    for unit_values in tag_facts.get("units", {}).values():
        for entry in unit_values:
            if entry.get("form") != "10-K" or entry.get("fp") != "FY":
                continue
            fy, val = entry.get("fy"), entry.get("val")
            if fy is None or val is None:
                continue
            filed = entry.get("filed", "")
            if fy not in best or filed > best[fy][0]:
                best[fy] = (filed, val)
    return {fy: val for fy, (_, val) in best.items()}


def _edgar_field_values(us_gaap: dict, field: str) -> dict[int, float]:
    """Merge a field's candidate tags in priority order: a year already
    filled by an earlier tag in the list is not overwritten by a later one,
    but a year missing from the first tag can still be filled by a later one
    -- the case a filer renaming its tag partway through its history needs."""
    values: dict[int, float] = {}
    for tag in _EDGAR_FIELD_TAGS[field]:
        tag_facts = us_gaap.get(tag)
        if not tag_facts:
            continue
        for fy, val in _edgar_annual_values(tag_facts).items():
            values.setdefault(fy, val)
    return values


@dataclass(frozen=True)
class AnnualStatements:
    by_year: dict[int, AnnualLineItems]

    @property
    def years(self) -> list[int]:
        return sorted(self.by_year)

    @property
    def revenue(self) -> list[float | None]:
        return [self.by_year[y].revenue for y in self.years]

    @property
    def net_income(self) -> list[float | None]:
        return [self.by_year[y].net_income for y in self.years]

    @property
    def diluted_eps(self) -> list[float | None]:
        return [self.by_year[y].diluted_eps for y in self.years]

    @property
    def gross_profit(self) -> list[float | None]:
        return [self.by_year[y].gross_profit for y in self.years]

    @property
    def operating_income(self) -> list[float | None]:
        return [self.by_year[y].operating_income for y in self.years]

    @property
    def free_cash_flow(self) -> list[float | None]:
        return [self.by_year[y].free_cash_flow for y in self.years]

    @property
    def total_debt(self) -> list[float | None]:
        return [self.by_year[y].total_debt for y in self.years]

    @property
    def stockholders_equity(self) -> list[float | None]:
        return [self.by_year[y].stockholders_equity for y in self.years]

    @property
    def operating_cash_flow(self) -> list[float | None]:
        return [self.by_year[y].operating_cash_flow for y in self.years]

    @classmethod
    def from_yfinance(
        cls,
        income: pd.DataFrame,
        balance: pd.DataFrame,
        cashflow: pd.DataFrame,
        fiscal_year_fn: Callable[[pd.Timestamp], int],
    ) -> "AnnualStatements":
        """years_available stays anchored to the income statement's fiscal
        years (matching the pre-existing contract); balance/cashflow are
        looked up per-year against that anchor, not zipped positionally.

        fiscal_year_fn has no default -- the pre-unification code hardcoded
        NSE's Apr-Mar rule regardless of market, which would have mislabeled
        every US company's years_available once real S&P data existed
        (US fiscal years are labeled by the calendar year they end in, no
        adjustment). Callers must be explicit about which market's rule
        applies; see MarketConfig.fiscal_year in screener.market."""
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
    def from_edgar(cls, facts: dict | None) -> "AnnualStatements":
        """facts is the raw SEC XBRL companyfacts payload (see
        screener.fetch.fetch_edgar_facts) -- {facts: {"us-gaap": {TAG:
        {units: {...}}}}}. Only the fields SNP's historical_trends actually
        uses are extracted (see _EDGAR_FIELD_TAGS); total_debt/
        stockholders_equity/free_cash_flow have no EDGAR tag mapping here and
        stay None, since those trends aren't computed for SNP (see
        data/SCHEMA.md's historical_trends metric table)."""
        us_gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
        per_field = {field: _edgar_field_values(us_gaap, field) for field in _EDGAR_FIELD_TAGS}

        years = sorted(set().union(*per_field.values()))
        by_year = {
            year: AnnualLineItems(**{field: per_field[field].get(year) for field in _EDGAR_FIELD_TAGS})
            for year in years
        }
        return cls(by_year=by_year)
