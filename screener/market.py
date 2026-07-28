"""MarketConfig — the value object unifying what previously differed ad hoc
between markets/nse.py and markets/snp.py: currency, fiscal-year rule, ticker
suffix, universe fetching, staleness policy, and optional per-market steps
(shareholding/credit-ratings enrichment, raw CSV export).

Named MarketConfig rather than Market to avoid colliding with the Market
StrEnum already exported by screener.freshness (a simple NSE/SNP discriminator
for quarter-lag policies) -- markets/nse.py imports both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from .config import (
    COMPANIES_DIR,
    INDICES_DIR,
    NSE_FAILED_TICKERS,
    RAW_DIR,
    SNP_COMPANIES_DIR,
    SNP_FAILED_TICKERS,
    SNP_INDICES_DIR,
)
from .fetch import fetch_nse500_tickers, fetch_sp500_universe
from .freshness import AgeDays, Market as MarketId, QuarterLag


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
    valid_modes: tuple[str, ...]
    fetch_universe: Callable[[], tuple[list[str], dict[str, dict] | None]]
    staleness_policies: Callable[[int], tuple]
    fetch_label: str = "companies"
    # NOTE: enrich.py's get_stale_symbols/process_symbols hardcode NSE's
    # COMPANIES_DIR internally and take no market parameter -- this field only
    # toggles *whether* the enrichment loop runs, safe today only because SNP's
    # tuple is empty. Populating it for SNP without first parameterizing
    # enrich.py by market would read/write against NSE's company directory.
    enrichment_datasets: tuple[str, ...] = ()
    raw_csv_dir: Path | None = None


def _nse_fiscal_year(d: date) -> int:
    """Indian FY: Apr 1 - Mar 31. FY ending Mar 2024 = FY2024."""
    return d.year + 1 if d.month >= 4 else d.year


def _snp_fiscal_year(d: date) -> int:
    """US convention: fiscal year is labeled by the calendar year its period
    ends in, regardless of the specific end month (e.g. Apple's Sept 30
    year-end is still "FY2024" if it ends in 2024) -- no adjustment needed."""
    return d.year


def _nse_universe() -> tuple[list[str], dict[str, dict]]:
    tickers, metadata = fetch_nse500_tickers()
    symbols = [t.replace(".NS", "") for t in tickers]
    return symbols, metadata


def _snp_universe() -> tuple[list[str], None]:
    companies = fetch_sp500_universe()
    return [c["symbol"] for c in companies], None


_NSE_QUARTER_POLICY = QuarterLag(field=("shareholding", "quarters", -1), market=MarketId.NSE)


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
    companies_dir=COMPANIES_DIR,
    indices_dir=INDICES_DIR,
    failed_tickers_path=NSE_FAILED_TICKERS,
    valid_modes=("full", "incremental", "quick", "sync-universe", "transform-only"),
    fetch_universe=_nse_universe,
    staleness_policies=_nse_staleness_policies,
    fetch_label="stocks",
    enrichment_datasets=("shareholding", "credit_ratings"),
    raw_csv_dir=RAW_DIR / "nse",
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
    valid_modes=("full", "incremental", "sync-universe", "rebuild"),
    fetch_universe=_snp_universe,
    staleness_policies=_snp_staleness_policies,
)
