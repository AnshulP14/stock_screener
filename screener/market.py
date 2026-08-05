"""MarketConfig — the value object driving run_pipeline for one market:
currency, fiscal-year rule, ticker suffix, universe fetching, staleness
policy, and optional per-market steps (shareholding/credit-ratings
enrichment, raw CSV export)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .config import (
    INDICES_DIR,
    NSE_COMPANIES_DIR,
    NSE_FAILED_TICKERS,
    RAW_DIR,
    SNP_COMPANIES_DIR,
    SNP_FAILED_TICKERS,
    SNP_INDICES_DIR,
)
from .fetch import fetch_nse500_tickers, fetch_sp500_universe
from .freshness import AgeDays, QuarterLag
from .freshness import NSE

# Both markets support the same refresh modes; run_pipeline validates against
# this so a typo'd mode fails loud instead of silently falling through.
ALL_MODES = ("quick-sync", "full-sync")


@dataclass(frozen=True)
class MarketConfig:
    id: str
    label: str
    currency: str
    ticker_suffix: str
    fiscal_year: Callable[[date], int]
    companies_dir: Path
    indices_dir: Path
    failed_tickers_path: Path
    fetch_universe: Callable[[], tuple[list[str], dict[str, dict] | None]]
    staleness_policies: Callable[[int], tuple]
    fetch_label: str = "companies"
    valid_modes: tuple[str, ...] = ALL_MODES
    enrichment_datasets: tuple[str, ...] = ()
    raw_csv_dir: Path | None = None
    uses_edgar: bool = False
    fetch_institutional_holders: bool = False
    metadata_fields: dict[str, str] = field(default_factory=dict)
    trend_series: tuple[tuple[str, str], ...] = ()


def _nse_fiscal_year(d: date) -> int:
    """Indian FY: Apr 1 - Mar 31"""
    return d.year + 1 if d.month >= 4 else d.year


def _snp_fiscal_year(d: date) -> int:
    """US FY: calendar year"""
    return d.year


def _nse_universe() -> tuple[list[str], dict[str, dict]]:
    tickers, metadata = fetch_nse500_tickers()
    symbols = [t.replace(".NS", "") for t in tickers]
    return symbols, metadata


def _snp_universe() -> tuple[list[str], dict[str, dict]]:
    companies = fetch_sp500_universe()
    metadata = {c["symbol"]: c for c in companies}
    return [c["symbol"] for c in companies], metadata


_NSE_QUARTER_POLICY = QuarterLag(field=("shareholding", "quarters", -1), market=NSE)


def _nse_staleness_policies(days_old: int) -> tuple:
    return (_NSE_QUARTER_POLICY, AgeDays(field=("current_snapshot", "as_of"), days=days_old))


def _snp_staleness_policies(days_old: int) -> tuple:
    return (AgeDays(field=("current_snapshot", "as_of"), days=days_old),)


NSE = MarketConfig(
    id="nse",
    label="NSE500",
    currency="INR",
    ticker_suffix=".NS",
    fiscal_year=_nse_fiscal_year,
    companies_dir=NSE_COMPANIES_DIR,
    indices_dir=INDICES_DIR,
    failed_tickers_path=NSE_FAILED_TICKERS,
    fetch_universe=_nse_universe,
    staleness_policies=_nse_staleness_policies,
    fetch_label="stocks",
    enrichment_datasets=("shareholding", "credit_ratings"),
    raw_csv_dir=RAW_DIR / "nse",
    metadata_fields={"isin": "isin_code", "nse_industry": "nse_industry"},
    trend_series=(
        ("revenue", "revenue"),
        ("net_income", "net_income"),
        ("eps", "diluted_eps"),
        ("gross_profit", "gross_profit"),
        ("operating_income", "operating_income"),
        ("free_cash_flow", "free_cash_flow"),
        ("total_debt", "total_debt"),
        ("stockholders_equity", "stockholders_equity"),
    ),
)

SNP = MarketConfig(
    id="snp",
    label="S&P 500",
    currency="USD",
    ticker_suffix="",
    fiscal_year=_snp_fiscal_year,
    companies_dir=SNP_COMPANIES_DIR,
    indices_dir=SNP_INDICES_DIR,
    failed_tickers_path=SNP_FAILED_TICKERS,
    fetch_universe=_snp_universe,
    staleness_policies=_snp_staleness_policies,
    uses_edgar=True,
    fetch_institutional_holders=True,
    metadata_fields={"gics_sector": "gics_sector", "gics_industry": "gics_industry"},
    trend_series=(
        ("revenue", "revenue"),
        ("net_income", "net_income"),
        ("eps", "diluted_eps"),
        ("gross_profit", "gross_profit"),
        ("operating_income", "operating_income"),
        ("operating_cash_flow", "operating_cash_flow"),
    ),
)


# ── Registry ─────────────────────────────────────────────────────────

MARKETS: dict[str, MarketConfig] = {"nse": NSE, "snp": SNP}
