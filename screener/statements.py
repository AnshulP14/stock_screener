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

import pandas as pd


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None  # NaN != NaN
    except (ValueError, TypeError):
        return None


def _process_annual_statement(df: pd.DataFrame) -> pd.DataFrame:
    """Transpose annual statement, collapse columns to fiscal years."""
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        transposed = df.T.copy()
        transposed.index = pd.to_datetime(transposed.index)
        transposed["fiscal_year"] = transposed.index.to_series().apply(
            lambda t: t.year + 1 if t.month >= 4 else t.year
        )
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

    @classmethod
    def from_yfinance(
        cls, income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame
    ) -> "AnnualStatements":
        """years_available stays anchored to the income statement's fiscal
        years (matching the pre-existing contract); balance/cashflow are
        looked up per-year against that anchor, not zipped positionally."""
        income = _process_annual_statement(income)
        balance = _process_annual_statement(balance)
        cashflow = _process_annual_statement(cashflow)
        sources = {"income": income, "balance": balance, "cashflow": cashflow}

        by_year = {}
        for year in sorted(income.columns):
            fields = {
                field: _cell(sources[source], label, year)
                for field, source, label in _FIELD_SOURCES
            }
            by_year[year] = AnnualLineItems(**fields)
        return cls(by_year=by_year)
